# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

try:
    import tkinter as _tk
    from tkinter import filedialog as _filedialog
except Exception:  # pragma: no cover
    _tk = None
    _filedialog = None

from aiwd.llama_server import LlamaServerConfig, LlamaServerProcess
from aiwd.citation_bank import CitationBankError, CitationBankIndexer
from aiwd.polish import PolishValidationError, build_polish_prompt, extract_json, validate_polish_json
from aiwd.rag_index import RagIndexError, RagIndexer
from version import VERSION

# Reuse the existing core pipeline without the Tk UI runtime.
from ai_word_detector import (  # type: ignore
    AcademicCorpus,
    LanguageDetector,
    LibraryManager,
    SEMANTIC_EMBED_BATCH,
    SEMANTIC_PROGRESS_EVERY_S,
    SemanticEmbedder,
    get_app_dir,
    get_settings_dir,
    normalize_soft_line_breaks_preserve_len,
    split_sentences_with_positions,
)


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _library_slug(library_manager: LibraryManager, library_name: str) -> str:
    if not library_name:
        return ""
    try:
        lib_path = library_manager.get_library_path(library_name)
        slug = os.path.splitext(os.path.basename(lib_path))[0]
        return slug or str(library_name or "").strip()
    except Exception:
        return str(library_name or "").strip()


@dataclass
class TaskStatus:
    id: str
    status: str  # "running" | "done" | "failed" | "canceled"
    stage: str
    done: int
    total: int
    detail: str
    started_at: float
    finished_at: float
    error: str


class TaskManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: Dict[str, TaskStatus] = {}
        self._cancel_events: Dict[str, threading.Event] = {}

    def create(self) -> TaskStatus:
        tid = f"t{int(time.time() * 1000)}"
        ts = TaskStatus(
            id=tid,
            status="running",
            stage="starting",
            done=0,
            total=0,
            detail="",
            started_at=time.time(),
            finished_at=0.0,
            error="",
        )
        with self._lock:
            self._tasks[tid] = ts
            self._cancel_events[tid] = threading.Event()
        return ts

    def get(self, tid: str) -> Optional[TaskStatus]:
        with self._lock:
            return self._tasks.get(tid)

    def cancel_event(self, tid: str) -> Optional[threading.Event]:
        with self._lock:
            return self._cancel_events.get(tid)

    def set_progress(self, tid: str, *, stage: str, done: int, total: int, detail: str = ""):
        with self._lock:
            t = self._tasks.get(tid)
            if not t or t.status != "running":
                return
            t.stage = stage or t.stage
            t.done = int(done or 0)
            t.total = int(total or 0)
            t.detail = (detail or "").strip()

    def finish(self, tid: str, *, status: str = "done", error: str = ""):
        with self._lock:
            t = self._tasks.get(tid)
            if not t:
                return
            t.status = status
            t.error = (error or "").strip()
            t.finished_at = time.time()

    def running_ids(self) -> list[str]:
        with self._lock:
            return [tid for tid, t in self._tasks.items() if t.status == "running"]

    def cancel_all(self):
        tids = self.running_ids()
        if not tids:
            return
        events: list[threading.Event] = []
        with self._lock:
            for tid in tids:
                ev = self._cancel_events.get(tid)
                if ev is not None:
                    events.append(ev)
        for ev in events:
            try:
                ev.set()
            except Exception:
                pass
        with self._lock:
            for tid in tids:
                t = self._tasks.get(tid)
                if t and t.status == "running":
                    t.status = "canceled"
                    t.finished_at = time.time()


class ClientRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._clients: Dict[str, float] = {}  # id -> last_seen_ts
        self._ever_had_client = False

    def register(self, client_id: str):
        now = time.time()
        with self._lock:
            self._clients[client_id] = now
            self._ever_had_client = True

    def heartbeat(self, client_id: str):
        now = time.time()
        with self._lock:
            if client_id:
                self._clients[client_id] = now
                self._ever_had_client = True

    def unregister(self, client_id: str):
        with self._lock:
            self._clients.pop(client_id, None)

    def active_count(self, *, ttl_s: float) -> int:
        now = time.time()
        alive = 0
        with self._lock:
            for ts in self._clients.values():
                if now - float(ts or 0.0) <= ttl_s:
                    alive += 1
        return alive

    def ever_had_client(self) -> bool:
        with self._lock:
            return bool(self._ever_had_client)

class RagManager:
    def __init__(self, *, data_dir: str, library_manager: LibraryManager, embedder: SemanticEmbedder):
        self.data_dir = data_dir
        self.library_manager = library_manager
        self.embedder = embedder
        self._lock = threading.Lock()
        self._sessions: Dict[str, Any] = {}  # slug -> session

    def _indexer(self, library_name: str) -> RagIndexer:
        slug = _library_slug(self.library_manager, library_name)
        if not slug:
            raise RuntimeError("library not selected")
        return RagIndexer(data_dir=self.data_dir, library_name=slug)

    def index_ready(self, library_name: str) -> bool:
        try:
            ix = self._indexer(library_name)
        except Exception:
            return False
        try:
            return os.path.exists(os.path.join(ix.storage_dir, "docstore.json"))
        except Exception:
            return False

    def build_index(
        self,
        *,
        library_name: str,
        pdf_folder: str,
        task_id: str,
        tasks: TaskManager,
        cancel_event: threading.Event,
    ):
        ix = self._indexer(library_name)

        def rag_progress(stage: str, done: int, total: int, detail: str = ""):
            # RagIndexer currently reports stages like: rag_extract / rag_embed / rag_done.
            st = (stage or "").strip().lower()
            if st.startswith("rag_"):
                st = st[4:]
            if st == "extract":
                tasks.set_progress(task_id, stage="rag_extract", done=done, total=total, detail=detail)
                return
            if st == "embed":
                tasks.set_progress(task_id, stage="rag_embed", done=done, total=total, detail=detail)
                return
            if st == "done":
                tasks.set_progress(task_id, stage="rag_done", done=done, total=total, detail=detail)
                return
            tasks.set_progress(task_id, stage=f"rag_{st or stage}", done=done, total=total, detail=detail)

        def embed_sentences(texts, progress_cb2, cancel_cb2):
            def _report(d, t):
                if progress_cb2:
                    try:
                        progress_cb2(d, t)
                    except Exception:
                        pass

            return self.embedder.embed(
                texts,
                batch_size=SEMANTIC_EMBED_BATCH,
                progress_callback=_report,
                progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                cancel_event=cancel_event,
            )

        def embed_query(q: str):
            vecs = self.embedder.embed(
                [q],
                batch_size=1,
                progress_callback=None,
                progress_every_s=0.0,
                cancel_event=cancel_event,
            )
            try:
                return vecs[0]
            except Exception:
                return vecs

        ix.build(
            pdf_folder,
            embed_sentences=embed_sentences,
            embed_query=embed_query,
            progress_cb=rag_progress,
            cancel_cb=cancel_event.is_set,
        )

        slug = _library_slug(self.library_manager, library_name)
        with self._lock:
            self._sessions.pop(slug, None)

    def search(self, *, library_name: str, query: str, top_k: int):
        slug = _library_slug(self.library_manager, library_name)
        if not slug:
            return []
        ix = self._indexer(library_name)

        def embed_query(q: str):
            vecs = self.embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
            try:
                return vecs[0]
            except Exception:
                return vecs

        with self._lock:
            sess = self._sessions.get(slug)
            if sess is None:
                sess = ix.create_session(embed_query=embed_query)
                self._sessions[slug] = sess

        try:
            return sess.search(query, top_k=top_k)
        except Exception:
            try:
                return ix.search(query, embed_query=embed_query, top_k=top_k)
            except Exception:
                return []


class CiteManager:
    def __init__(self, *, data_dir: str, library_manager: LibraryManager, embedder: SemanticEmbedder):
        self.data_dir = data_dir
        self.library_manager = library_manager
        self.embedder = embedder
        self._lock = threading.Lock()
        self._sessions: Dict[str, Any] = {}  # slug -> session

    def _indexer(self, library_name: str) -> CitationBankIndexer:
        slug = _library_slug(self.library_manager, library_name)
        if not slug:
            raise RuntimeError("library not selected")
        return CitationBankIndexer(data_dir=self.data_dir, library_name=slug)

    def index_ready(self, library_name: str) -> bool:
        try:
            ix = self._indexer(library_name)
        except Exception:
            return False
        return ix.index_ready()

    def build_index(
        self,
        *,
        library_name: str,
        pdf_folder: str,
        task_id: str,
        tasks: TaskManager,
        cancel_event: threading.Event,
        max_pages: Optional[int] = None,
    ) -> dict:
        ix = self._indexer(library_name)

        def cite_progress(stage: str, done: int, total: int, detail: str = ""):
            tasks.set_progress(task_id, stage=(stage or "cite").strip().lower(), done=done, total=total, detail=detail)

        def embed_sentences(texts, progress_cb2, cancel_cb2):
            def _report(d, t):
                if progress_cb2:
                    try:
                        progress_cb2(d, t)
                    except Exception:
                        pass

            return self.embedder.embed(
                texts,
                batch_size=SEMANTIC_EMBED_BATCH,
                progress_callback=_report,
                progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                cancel_event=cancel_event,
            )

        stats = ix.build(
            pdf_root=pdf_folder,
            embed_sentences=embed_sentences,
            progress_cb=cite_progress,
            cancel_cb=cancel_event.is_set,
            max_pages=max_pages,
            stop_at_references=True,
        )
        slug = _library_slug(self.library_manager, library_name)
        with self._lock:
            self._sessions.pop(slug, None)
        return {"ok": True, "stats": stats.__dict__}

    def search(self, *, library_name: str, query: str, top_k: int):
        slug = _library_slug(self.library_manager, library_name)
        if not slug:
            return []
        ix = self._indexer(library_name)

        def embed_query(q: str):
            vecs = self.embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
            try:
                return vecs[0]
            except Exception:
                return vecs

        with self._lock:
            sess = self._sessions.get(slug)
            if sess is None:
                sess = ix.create_session(embed_query=embed_query)
                self._sessions[slug] = sess

        try:
            return sess.search(query, top_k=top_k)
        except Exception:
            with self._lock:
                self._sessions.pop(slug, None)
            try:
                return ix.search(query, embed_query=embed_query, top_k=top_k)
            except Exception:
                return []


class LazySemanticEmbedder:
    """Lazy-load SemanticEmbedder to keep web startup fast."""

    def __init__(self, model_dir: str, model_id: str = ""):
        self.model_dir = model_dir
        self._model_id = model_id or os.path.basename(os.path.normpath(model_dir or "")) or "semantic"
        self._lock = threading.Lock()
        self._impl: Optional[SemanticEmbedder] = None

    def _get(self) -> SemanticEmbedder:
        impl = self._impl
        if impl is not None:
            return impl
        with self._lock:
            impl2 = self._impl
            if impl2 is None:
                impl2 = SemanticEmbedder(self.model_dir, model_id=self._model_id)
                self._impl = impl2
            return impl2

    @property
    def model_id(self) -> str:
        impl = self._impl
        return getattr(impl, "model_id", "") or self._model_id

    def model_fingerprint(self) -> dict:
        return self._get().model_fingerprint()

    def embed(self, *args, **kwargs):
        return self._get().embed(*args, **kwargs)


class LLMManager:
    def __init__(self, *, data_dir: str):
        self.data_dir = data_dir
        self._lock = threading.Lock()
        self._chat_lock = threading.Lock()
        self._proc: Optional[LlamaServerProcess] = None
        self._cfg: Optional[LlamaServerConfig] = None

    def _default_server_path(self) -> str:
        return os.path.join(get_app_dir(), "models", "llm", "llama-server.exe")

    def _default_model_path(self) -> str:
        return os.path.join(get_app_dir(), "models", "llm", "qwen2.5-3b-instruct-q4_k_m.gguf")

    def status(self) -> dict:
        with self._lock:
            proc = self._proc
        server_path = getattr(getattr(proc, "cfg", None), "server_path", "") or self._default_server_path()
        model_path = getattr(getattr(proc, "cfg", None), "model_path", "") or self._default_model_path()
        server_ok = bool(server_path) and os.path.exists(server_path)
        model_ok = bool(model_path) and os.path.exists(model_path)
        running = False
        base_url = ""
        try:
            running = bool(proc and proc.is_running() and proc.health(timeout_s=0.6))
            base_url = proc.base_url if proc else ""
        except Exception:
            running = False
        return {
            "server_path": server_path,
            "model_path": model_path,
            "server_ok": server_ok,
            "model_ok": model_ok,
            "running": running,
            "base_url": base_url,
        }

    def configure(
        self,
        *,
        server_path: str,
        model_path: str,
        ctx_size: int = 2048,
        threads: int = 4,
        n_gpu_layers: int = 0,
        sleep_idle_seconds: int = 300,
    ):
        cfg = LlamaServerConfig(
            server_path=server_path,
            model_path=model_path,
            ctx_size=max(512, min(int(ctx_size or 2048), 8192)),
            threads=max(1, min(int(threads or 4), 64)),
            n_gpu_layers=max(0, int(n_gpu_layers or 0)),
            sleep_idle_seconds=max(0, int(sleep_idle_seconds or 300)),
        )
        with self._lock:
            if self._proc is not None:
                try:
                    self._proc.stop()
                except Exception:
                    pass
            log_path = os.path.join(self.data_dir, "llm", "llama_server.log")
            self._proc = LlamaServerProcess(cfg, log_path=log_path)
            self._cfg = cfg

    def ensure_started(self, timeout_s: float = 45.0) -> bool:
        with self._lock:
            proc = self._proc
            cfg = self._cfg
        if proc is None or cfg is None:
            self.configure(server_path=self._default_server_path(), model_path=self._default_model_path())
            with self._lock:
                proc = self._proc
        if proc is None:
            return False
        try:
            return bool(proc.ensure_started(timeout_s=timeout_s))
        except Exception:
            return False

    def stop(self):
        with self._lock:
            proc = self._proc
            self._proc = None
            self._cfg = None
        if proc is None:
            return
        try:
            proc.stop()
        except Exception:
            pass

    def chat(
        self,
        *,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 900,
        response_format: Optional[dict] = None,
        timeout_s: float = 180.0,
    ):
        with self._chat_lock:
            with self._lock:
                proc = self._proc
            if proc is None:
                raise RuntimeError("LLM not configured")
            return proc.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                timeout_s=timeout_s,
            )


def create_app() -> FastAPI:
    app = FastAPI(title="TopHumanWriting", version=str(VERSION or "0.0.0"))
    # Allow the local file-based splash screen (file://) to probe /api/health via fetch.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    library_manager = LibraryManager()

    semantic_dir = os.path.join(get_app_dir(), "models", "semantic")
    if not os.path.exists(semantic_dir):
        raise RuntimeError(f"Missing semantic model folder: {semantic_dir}")
    embedder = LazySemanticEmbedder(semantic_dir, model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2")

    tasks = TaskManager()
    rag = RagManager(data_dir=get_settings_dir(), library_manager=library_manager, embedder=embedder)
    cite = CiteManager(data_dir=get_settings_dir(), library_manager=library_manager, embedder=embedder)
    llm = LLMManager(data_dir=get_settings_dir())
    clients = ClientRegistry()

    try:
        app.state.tasks = tasks
        app.state.llm = llm
        app.state.clients = clients
        app.state.started_at = time.time()
        app.state.exit_requested = False
    except Exception:
        pass

    def _request_exit(reason: str = "") -> None:
        reason = (reason or "").strip()
        if getattr(app.state, "exit_requested", False):
            return
        try:
            app.state.exit_requested = True
            app.state.exit_reason = reason
        except Exception:
            pass
        try:
            tasks.cancel_all()
        except Exception:
            pass
        try:
            llm.stop()
        except Exception:
            pass
        server = getattr(app.state, "uvicorn_server", None)
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
            return
        # Fallback: force-exit the current process (works even when running via `uvicorn webapp.app:app`).
        def _force_exit():
            try:
                time.sleep(0.6)
            except Exception:
                pass
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()

    @app.on_event("startup")
    def _startup_watchdog():
        def loop():
            ttl_s = float(os.environ.get("AIW_CLIENT_TTL_S", "15") or 15)
            grace_s = float(os.environ.get("AIW_SHUTDOWN_GRACE_S", "4") or 4)
            check_s = float(os.environ.get("AIW_SHUTDOWN_CHECK_S", "0.8") or 0.8)
            armed_at: float | None = None
            while True:
                if getattr(app.state, "exit_requested", False):
                    return
                if not clients.ever_had_client():
                    time.sleep(check_s)
                    continue
                active = clients.active_count(ttl_s=ttl_s)
                if active > 0:
                    armed_at = None
                else:
                    if armed_at is None:
                        armed_at = time.time()
                    if time.time() - armed_at >= grace_s:
                        _request_exit("no_clients")
                        return
                time.sleep(check_s)

        threading.Thread(target=loop, daemon=True).start()

    def _pick_folder_dialog() -> str:
        # Prefer Tk (best UX) when available.
        if _tk is not None and _filedialog is not None:
            try:
                root = _tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
            except Exception:
                root = None
            try:
                return (_filedialog.askdirectory(title="Select PDF folder") or "").strip()
            finally:
                try:
                    if root is not None:
                        root.destroy()
                except Exception:
                    pass

        # Portable Python (python.org embeddable) on Windows doesn't ship tkinter.
        # Fallback to a native folder dialog via Windows PowerShell.
        if os.name == "nt":
            try:
                ps = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$form.ShowInTaskbar = $false
$form.WindowState = [System.Windows.Forms.FormWindowState]::Minimized
$form.Show()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = "Select PDF folder"
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog($form)
$form.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) { $dialog.SelectedPath }
"""
                creationflags = 0
                try:
                    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
                except Exception:
                    creationflags = 0
                startupinfo = None
                try:
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 1) or 1)
                    startupinfo.wShowWindow = 0
                except Exception:
                    startupinfo = None
                cp = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-STA", "-Command", ps],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=120,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                path = (cp.stdout or "").strip()
                if path:
                    return path
            except Exception:
                return ""

        return ""

    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/api/health")
    def health():
        return {"ok": True, "time": time.time()}

    @app.get("/api/app/status")
    def app_status():
        ttl_s = float(os.environ.get("AIW_CLIENT_TTL_S", "15") or 15)
        return {
            "ok": True,
            "pid": os.getpid(),
            "clients": clients.active_count(ttl_s=ttl_s),
            "ever_had_client": clients.ever_had_client(),
        }

    @app.post("/api/app/exit")
    def app_exit(payload: dict = Body(default={})):
        reason = (payload.get("reason", "") or "").strip()
        _request_exit(reason or "user")
        return {"ok": True}

    @app.get("/api/libraries")
    def list_libraries():
        libs = library_manager.list_libraries()
        return {"libraries": libs}

    @app.post("/api/libraries")
    def create_library(payload: dict = Body(...)):
        name = (payload.get("name", "") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        if library_manager.library_exists(name):
            raise HTTPException(status_code=409, detail="library exists")
        path = library_manager.create_library(name)
        return {"name": name, "path": path}

    @app.post("/api/dialog/pick_folder")
    def pick_folder():
        path = _pick_folder_dialog()
        return {"folder": path}

    @app.post("/api/client/register")
    def client_register(request: Request, payload: dict = Body(default={})):
        client_id = (payload.get("client_id", "") or "").strip()
        if not client_id:
            client_id = "c" + secrets.token_urlsafe(10)
        clients.register(client_id)
        return {"ok": True, "client_id": client_id}

    @app.post("/api/client/heartbeat")
    def client_heartbeat(payload: dict = Body(default={})):
        client_id = (payload.get("client_id", "") or "").strip()
        if client_id:
            clients.heartbeat(client_id)
        return {"ok": True}

    @app.post("/api/client/unregister")
    def client_unregister(payload: dict = Body(default={})):
        client_id = (payload.get("client_id", "") or "").strip()
        if client_id:
            clients.unregister(client_id)
        return {"ok": True}

    @app.get("/api/llm/status")
    def llm_status():
        return llm.status()

    @app.post("/api/llm/stop")
    def llm_stop():
        llm.stop()
        return llm.status()

    @app.post("/api/llm/test")
    def llm_test(payload: dict = Body(default={})):
        server_path = (payload.get("server_path", "") or "").strip() or llm.status().get("server_path", "")
        model_path = (payload.get("model_path", "") or "").strip() or llm.status().get("model_path", "")
        ctx_size = int(payload.get("ctx_size", 2048) or 2048)
        threads = int(payload.get("threads", 4) or 4)
        n_gpu_layers = int(payload.get("n_gpu_layers", 0) or 0)
        sleep_idle_seconds = int(payload.get("sleep_idle_seconds", 300) or 300)
        llm.configure(
            server_path=server_path,
            model_path=model_path,
            ctx_size=ctx_size,
            threads=threads,
            n_gpu_layers=n_gpu_layers,
            sleep_idle_seconds=sleep_idle_seconds,
        )
        ok = llm.ensure_started(timeout_s=45.0)
        if not ok:
            raise HTTPException(status_code=500, detail="failed to start llama-server")
        status, resp = llm.chat(
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": "{\"ok\": true}"},
            ],
            temperature=0.0,
            max_tokens=32,
            response_format={"type": "json_object"},
            timeout_s=30.0,
        )
        return {"ok": bool(int(status or 0) == 200), "http": int(status or 0), "status": llm.status(), "resp": resp}

    @app.post("/api/library/build")
    def build_library(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_folder = (payload.get("folder", "") or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_folder or not os.path.exists(pdf_folder):
            raise HTTPException(status_code=400, detail="folder not found")

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        def worker():
            try:
                lib_path = library_manager.get_library_path(library_name)
                corpus = AcademicCorpus(lib_path)

                def pdf_progress(done: int, total: int, detail: str = ""):
                    tasks.set_progress(ts.id, stage="pdf_extract", done=done, total=total, detail=detail)

                def semantic_progress(done: int, total: int, detail: str = ""):
                    tasks.set_progress(ts.id, stage="semantic_embed", done=done, total=total, detail=str(detail or ""))

                count = corpus.process_pdf_folder(
                    pdf_folder,
                    pdf_progress,
                    semantic_embedder=embedder,
                    semantic_progress_callback=semantic_progress,
                    syntax_analyzer=None,
                    syntax_progress_callback=None,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return

                rag.build_index(
                    library_name=library_name,
                    pdf_folder=pdf_folder,
                    task_id=ts.id,
                    tasks=tasks,
                    cancel_event=cancel_event,
                )

                corpus.save_vocabulary()
                tasks.finish(ts.id, status="done", error="")
            except RagIndexError as e:
                tasks.finish(ts.id, status="failed", error=str(e))
            except Exception:
                tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str):
        t = tasks.get(task_id)
        if t is None:
            raise HTTPException(status_code=404, detail="task not found")
        return asdict(t)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str):
        ev = tasks.cancel_event(task_id)
        if ev is None:
            raise HTTPException(status_code=404, detail="task not found")
        ev.set()
        t = tasks.get(task_id)
        if t and t.status == "running":
            tasks.finish(task_id, status="canceled", error="")
        return {"ok": True}

    @app.get("/api/library/status")
    def library_status(library: str):
        library = (library or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        info = library_manager.get_library_info(library)
        base = os.path.splitext(info.get("path", "") or "")[0]
        has_sem = bool(base) and os.path.exists(base + ".sentences.json") and os.path.exists(base + ".embeddings.npy")
        has_rag = rag.index_ready(library)
        has_cite = cite.index_ready(library)
        return {"library": library, "semantic_index": has_sem, "rag_index": has_rag, "cite_index": has_cite, "info": info}

    @app.get("/api/cite/status")
    def cite_status(library: str):
        library = (library or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        try:
            ix = cite._indexer(library)  # type: ignore[attr-defined]
        except Exception:
            raise HTTPException(status_code=400, detail="library not found")
        return {"library": library, "cite_index": ix.index_ready(), "manifest": ix.load_manifest()}

    @app.post("/api/cite/build")
    def build_cite_bank(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_folder = (payload.get("folder", "") or "").strip()
        try:
            max_pages = payload.get("max_pages", None)
            max_pages = int(max_pages) if max_pages is not None else None
        except Exception:
            max_pages = None

        if not library_name:
            raise HTTPException(status_code=400, detail="library required")

        if not pdf_folder:
            # Default to the folder used by the RAG index.
            try:
                ix_rag = rag._indexer(library_name)  # type: ignore[attr-defined]
            except Exception:
                raise HTTPException(status_code=400, detail="rag manifest missing (build library first)")
            manifest = {}
            try:
                if os.path.exists(ix_rag.manifest_path):
                    import json as _json

                    with open(ix_rag.manifest_path, "r", encoding="utf-8") as f:
                        manifest = _json.load(f)
            except Exception:
                manifest = {}
            pdf_folder = (manifest.get("pdf_root", "") if isinstance(manifest, dict) else "") or ""

        if not pdf_folder or not os.path.exists(pdf_folder):
            raise HTTPException(status_code=400, detail="folder not found")

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        def worker():
            try:
                cite.build_index(
                    library_name=library_name,
                    pdf_folder=pdf_folder,
                    task_id=ts.id,
                    tasks=tasks,
                    cancel_event=cancel_event,
                    max_pages=max_pages,
                )
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return
                tasks.finish(ts.id, status="done", error="")
            except CitationBankError as e:
                tasks.finish(ts.id, status="failed", error=str(e))
            except Exception:
                tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.post("/api/cite/search")
    def cite_search(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        query = (payload.get("query", "") or "").strip()
        top_k = int(payload.get("top_k", 8) or 8)
        top_k = max(1, min(top_k, 20))
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not query:
            raise HTTPException(status_code=400, detail="query required")
        if not cite.index_ready(library_name):
            raise HTTPException(status_code=400, detail="citation bank missing (build it first)")

        hits = cite.search(library_name=library_name, query=query, top_k=top_k)
        return {"hits": [asdict(h) for h in hits], "count": len(hits)}

    @app.get("/api/cite/references")
    def cite_references(library: str, pdf: str = "", limit: int = 800):
        library = (library or "").strip()
        pdf = (pdf or "").strip().replace("\\", "/")
        limit = max(1, min(int(limit or 0), 6000))
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        try:
            ix = cite._indexer(library)  # type: ignore[attr-defined]
        except Exception:
            raise HTTPException(status_code=400, detail="library not found")

        refs = ix.load_references()
        if pdf:
            refs = [r for r in refs if str(r.get("pdf", "") or "").replace("\\", "/") == pdf]
        if len(refs) > limit:
            refs = refs[:limit]
        return {"library": library, "pdf": pdf, "references": refs, "count": len(refs)}

    @app.post("/api/align/scan")
    def align_scan(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        raw_text = (payload.get("text", "") or "").rstrip("\n")
        top_k = int(payload.get("top_k", 6) or 6)
        max_items = int(payload.get("max_items", 220) or 220)
        top_k = max(2, min(int(top_k), 12))
        max_items = max(30, min(int(max_items), 3000))
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not raw_text.strip():
            raise HTTPException(status_code=400, detail="text required")
        if not rag.index_ready(library_name):
            raise HTTPException(status_code=400, detail="rag index missing (build library first)")

        analysis_text = normalize_soft_line_breaks_preserve_len(raw_text)
        lang = LanguageDetector.detect(analysis_text)
        sents = split_sentences_with_positions(analysis_text, lang)

        candidates = []
        for sent, s, e in sents:
            st = (sent or "").strip()
            if len(st) < 20:
                continue
            if len(st) > 1200:
                st = st[:1200].rstrip() + "…"
            candidates.append((st, int(s), int(e)))
            if len(candidates) >= max_items:
                break

        items = []
        for (st, s, e) in candidates:
            ex = rag.search(library_name=library_name, query=st, top_k=top_k)
            score = float(ex[0][0]) if ex else 0.0
            exemplars = []
            for sc, node in (ex[:top_k] if ex else []):
                try:
                    pdf = getattr(node, "pdf", "") or ""
                    page = int(getattr(node, "page", 0) or 0)
                    txt = (getattr(node, "text", "") or "").strip()
                except Exception:
                    pdf, page, txt = "", 0, ""
                exemplars.append({"score": float(sc or 0.0), "pdf": pdf, "page": page, "text": txt})
            pct = int(max(0.0, min(1.0, score)) * 100)
            items.append({"text": st, "start": s, "end": e, "score": score, "pct": pct, "exemplars": exemplars})

        items.sort(key=lambda d: float(d.get("score", 0.0) or 0.0))
        return {"items": items}

    @app.post("/api/align/polish")
    def align_polish(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        selected = (payload.get("selected_text", "") or "").strip()
        top_k = int(payload.get("top_k", 8) or 8)
        do_generate = bool(payload.get("generate", False))
        top_k = max(2, min(int(top_k), 12))
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not selected or len(selected) < 8:
            raise HTTPException(status_code=400, detail="selected_text too short")
        if not rag.index_ready(library_name):
            raise HTTPException(status_code=400, detail="rag index missing (build library first)")

        results = rag.search(library_name=library_name, query=selected, top_k=top_k)
        exemplars = []
        c_list = []
        allowed_quotes: Dict[str, str] = {}
        for i, (sc, node) in enumerate(results[:top_k], start=1):
            cid = f"C{i}"
            try:
                pdf = getattr(node, "pdf", "") or ""
                page = int(getattr(node, "page", 0) or 0)
                txt = (getattr(node, "text", "") or "").strip()
            except Exception:
                pdf, page, txt = "", 0, ""
            exemplars.append({"id": cid, "score": float(sc or 0.0), "pdf": pdf, "page": page, "text": txt})
            excerpt = f"[{pdf}#p{page}] {txt}"
            c_list.append((cid, excerpt))
            allowed_quotes[cid] = excerpt

        out: Dict[str, Any] = {"selected_text": selected, "exemplars": exemplars}
        if not do_generate:
            return out

        if not llm.ensure_started(timeout_s=45.0):
            raise HTTPException(status_code=500, detail="failed to start llama-server")

        lang = LanguageDetector.detect(selected)
        prompt = build_polish_prompt(selected_text=selected, citations=c_list, language=lang)
        messages = [
            {"role": "system", "content": "Return STRICT JSON only."},
            {"role": "user", "content": prompt},
        ]

        temperature = float(payload.get("temperature", 0.0) or 0.0)
        max_tokens_requested = int(payload.get("max_tokens", 900) or 900)
        max_tokens = max(64, min(int(max_tokens_requested), 2048))
        retries = int(payload.get("retries", 2) or 2)
        retries = max(0, min(retries, 3))

        last_err = ""
        parsed = None
        for attempt in range(1, retries + 2):
            status, resp = llm.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout_s=180.0,
            )
            if int(status or 0) != 200 or not isinstance(resp, dict):
                last_err = f"http {status}"
                continue
            content = ""
            try:
                choices = resp.get("choices", [])
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
                    if isinstance(msg, dict):
                        content = (msg.get("content", "") or "").strip()
            except Exception:
                content = ""
            data = extract_json(content) if content else None
            if not isinstance(data, dict):
                last_err = "bad json"
                # If the user requested a very small output budget, the model can get truncated.
                # Auto-increase a bit to avoid a frustrating "bad json" loop.
                if max_tokens < 900:
                    if attempt <= 1:
                        max_tokens = min(2048, max(600, max_tokens * 2))
                    else:
                        max_tokens = min(2048, 900)
                continue
            try:
                parsed = validate_polish_json(
                    data,
                    allowed_citation_ids=[cid for cid, _ in c_list],
                    allowed_quotes=allowed_quotes,
                    selected_text=selected,
                )
                break
            except PolishValidationError as e:
                last_err = str(e)
                repair_prompt = "\n\n".join(
                    [
                        "Your previous output was invalid.",
                        f"VALIDATION_ERROR: {last_err}",
                        "Fix it and output STRICT JSON ONLY matching OUTPUT_SCHEMA.",
                        "Do not add any extra keys. Do not include markdown. Ensure the JSON is complete and valid.",
                        "",
                        prompt,
                    ]
                ).strip()
                messages = [
                    {"role": "system", "content": "Return STRICT JSON only."},
                    {"role": "user", "content": repair_prompt},
                ]
                continue

        if parsed is None:
            detail = f"LLM output invalid: {last_err}"
            if last_err == "bad json":
                detail += " (output may be truncated; try increasing max_tokens)"
            raise HTTPException(status_code=500, detail=detail)

        out["result"] = {
            "language": getattr(parsed, "language", "mixed"),
            "diagnosis": [
                {
                    "title": d.title,
                    "problem": d.problem,
                    "suggestion": d.suggestion,
                    "evidence": [asdict(c) for c in (d.evidence or [])],
                }
                for d in (getattr(parsed, "diagnosis", []) or [])
            ],
            "variants": [
                {
                    "level": v.level,
                    "rewrite": v.rewrite,
                    "changes": list(v.changes or []),
                    "citations": [asdict(c) for c in (v.citations or [])],
                }
                for v in (getattr(parsed, "variants", []) or [])
            ],
        }
        return out

    @app.post("/api/library/open_pdf")
    def open_library_pdf(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_rel = (payload.get("pdf", "") or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_rel:
            raise HTTPException(status_code=400, detail="pdf required")

        pdf_root = ""
        # Prefer the RAG manifest when available.
        try:
            ix = rag._indexer(library_name)  # type: ignore[attr-defined]
            manifest = {}
            try:
                if os.path.exists(ix.manifest_path):
                    import json as _json

                    with open(ix.manifest_path, "r", encoding="utf-8") as f:
                        manifest = _json.load(f)
            except Exception:
                manifest = {}
            pdf_root = (manifest.get("pdf_root", "") if isinstance(manifest, dict) else "") or ""
        except Exception:
            pdf_root = ""

        # Fallback: use citation-bank manifest (so Cite can work without RAG).
        if not pdf_root:
            try:
                ix2 = cite._indexer(library_name)  # type: ignore[attr-defined]
                manifest2 = ix2.load_manifest()
                pdf_root = (manifest2.get("pdf_root", "") if isinstance(manifest2, dict) else "") or ""
            except Exception:
                pdf_root = ""

        if not pdf_root:
            raise HTTPException(status_code=400, detail="pdf root missing (build library/citation bank first)")

        rel = pdf_rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(pdf_root, rel))
        try:
            root_abs = os.path.abspath(pdf_root).lower()
            full_abs = os.path.abspath(full).lower()
            if not (full_abs == root_abs or full_abs.startswith(root_abs + os.sep.lower())):
                raise HTTPException(status_code=400, detail="invalid pdf path")
        except HTTPException:
            raise
        except Exception:
            pass

        if not os.path.exists(full):
            raise HTTPException(status_code=404, detail=f"pdf not found: {full}")
        try:
            os.startfile(full)  # type: ignore[attr-defined]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "path": full}

    @app.post("/api/open")
    def open_path(payload: dict = Body(...)):
        path = (payload.get("path", "") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="path required")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="not found")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True}

    return app


app = create_app()
