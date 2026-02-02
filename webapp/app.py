# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import secrets
import subprocess
import shutil
import hashlib
import threading
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, Response
    from fastapi.staticfiles import StaticFiles
except Exception as e:  # pragma: no cover
    raise RuntimeError("Missing web dependencies. Install: pip install 'tophumanwriting[web]'") from e

from aiwd.openai_compat import OpenAICompatClient, OpenAICompatConfig, extract_first_content, mask_secret, normalize_base_url
from aiwd.citation_bank import CitationBankError, CitationBankIndexer
from aiwd.cite_check import CiteCheckConfig, CiteCheckRunner
from aiwd.oa_lookup import (
    crossref_search,
    download_pdf as download_oa_pdf,
    pick_best_oa_candidate,
    sanitize_filename,
    semantic_scholar_search,
)
from aiwd.audit import run_full_paper_audit
from aiwd.llm_budget import LLMBudget
from aiwd.llm_review import run_llm_audit_pack
from aiwd.materials import MaterialsError, MaterialsIndexer, build_material_doc
from aiwd.polish import PolishValidationError, build_polish_prompt, extract_json, validate_polish_json
from aiwd.rag_index import RagIndexError, RagIndexer
from aiwd.report import audit_to_markdown
from aiwd.review_coverage import ReviewCoverageStore
from tophumanwriting._version import VERSION

# Reuse the existing core pipeline without the Tk UI runtime.
from ai_word_detector import (  # type: ignore
    AcademicCorpus,
    LanguageDetector,
    LibraryManager,
    SEMANTIC_EMBED_BATCH,
    SEMANTIC_PROGRESS_EVERY_S,
    SemanticEmbedder,
    Settings,
    UDPipeSyntaxAnalyzer,
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
            if not os.path.exists(os.path.join(ix.storage_dir, "docstore.json")):
                return False
        except Exception:
            return False

        # Guardrail: a previous failed build could leave an "empty but present" index.
        # Prefer manifest node_count when available; fall back to non-empty nodes.jsonl.
        try:
            if os.path.exists(ix.manifest_path):
                with open(ix.manifest_path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
                if isinstance(manifest, dict):
                    try:
                        node_count = int(manifest.get("node_count", 0) or 0)
                    except Exception:
                        node_count = 0
                    if node_count <= 0:
                        return False
        except Exception:
            pass
        try:
            if os.path.exists(ix.nodes_path) and os.path.getsize(ix.nodes_path) <= 0:
                return False
        except Exception:
            pass
        return True

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


class MaterialsManager:
    def __init__(self, *, data_dir: str, library_manager: LibraryManager, llm_api: "LLMApiManager"):
        self.data_dir = data_dir
        self.library_manager = library_manager
        self.llm_api = llm_api

    def _indexer(self, library_name: str) -> MaterialsIndexer:
        slug = _library_slug(self.library_manager, library_name)
        if not slug:
            raise MaterialsError("library not selected")
        return MaterialsIndexer(data_dir=self.data_dir, library_name=slug)

    def index_ready(self, library_name: str) -> bool:
        try:
            ix = self._indexer(library_name)
        except Exception:
            return False
        return ix.index_ready()

    def load_manifest(self, library_name: str) -> dict:
        try:
            ix = self._indexer(library_name)
            return ix.load_manifest()
        except Exception:
            return {}

    def build_index(
        self,
        *,
        library_name: str,
        pdf_root: str,
        task_id: str,
        tasks: TaskManager,
        cancel_event: threading.Event,
        use_llm: bool = False,
        llm_overrides: Optional[dict] = None,
        max_pdfs: Optional[int] = None,
    ) -> dict:
        ix = self._indexer(library_name)

        def mat_progress(stage: str, done: int, total: int, detail: str = ""):
            tasks.set_progress(task_id, stage=(stage or "materials").strip().lower(), done=done, total=total, detail=detail)

        llm_client = None
        if use_llm:
            try:
                cfg = self.llm_api.resolve_config(overrides=llm_overrides or {})
                if cfg and (cfg.base_url and cfg.model):
                    llm_client = OpenAICompatClient(cfg)
            except Exception:
                llm_client = None

        stats = ix.build(
            pdf_root=pdf_root,
            llm=llm_client,
            use_llm=bool(use_llm and llm_client is not None),
            progress_cb=mat_progress,
            cancel_cb=cancel_event.is_set,
            max_pdfs=max_pdfs,
        )
        return {"ok": True, "stats": stats.__dict__ if hasattr(stats, "__dict__") else {}}


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


class LLMApiManager:
    """
    OpenAI-compatible API settings (base_url / model / api_key).

    - Defaults read from environment variables:
        TOPHUMANWRITING_LLM_API_KEY / _BASE_URL / _MODEL
        SKILL_LLM_API_KEY / _BASE_URL / _MODEL
        OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
    - Optional overrides stored in settings.json (via ai_word_detector.Settings).
    """

    def __init__(self, *, settings: Settings):
        self.settings = settings

    @staticmethod
    def _first_env(*names: str) -> str:
        for n in names:
            v = os.environ.get(n, "")
            if v and v.strip():
                return v.strip()
        return ""

    def _resolve_value(self, *, key: str, env_names: list[str], default: str = "") -> tuple[str, str]:
        v0 = (self.settings.get(key, "") or "").strip()
        if v0:
            return v0, "settings"
        v1 = self._first_env(*env_names)
        if v1:
            # Return the first hit name for UI clarity.
            for n in env_names:
                vv = os.environ.get(n, "")
                if vv and vv.strip():
                    return vv.strip(), f"env:{n}"
        return (default or "").strip(), "default"

    def resolve_config(self, *, overrides: Optional[dict] = None) -> OpenAICompatConfig:
        overrides = overrides or {}

        api_key, _ = self._resolve_value(
            key="llm_api_key",
            env_names=[
                "TOPHUMANWRITING_LLM_API_KEY",
                "SKILL_LLM_API_KEY",
                "OPENAI_API_KEY",
            ],
            default="",
        )
        base_url, _ = self._resolve_value(
            key="llm_api_base_url",
            env_names=[
                "TOPHUMANWRITING_LLM_BASE_URL",
                "SKILL_LLM_BASE_URL",
                "OPENAI_BASE_URL",
            ],
            default="https://api.openai.com/v1",
        )
        model, _ = self._resolve_value(
            key="llm_api_model",
            env_names=[
                "TOPHUMANWRITING_LLM_MODEL",
                "SKILL_LLM_MODEL",
                "OPENAI_MODEL",
            ],
            default="gpt-4o-mini",
        )

        # Allow per-request overrides (useful for testing without persisting secrets).
        api_key = (str(overrides.get("api_key", "") or "").strip()) or api_key
        base_url = (str(overrides.get("base_url", "") or "").strip()) or base_url
        model = (str(overrides.get("model", "") or "").strip()) or model

        base_url = normalize_base_url(base_url)

        return OpenAICompatConfig(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=float(overrides.get("timeout_s", 60.0) or 60.0),
            max_retries=int(overrides.get("max_retries", 5) or 5),
            base_retry_delay_s=float(overrides.get("base_retry_delay_s", 0.9) or 0.9),
            max_retry_delay_s=float(overrides.get("max_retry_delay_s", 10.0) or 10.0),
        )

    def status(self) -> dict:
        api_key, api_key_src = self._resolve_value(
            key="llm_api_key",
            env_names=["TOPHUMANWRITING_LLM_API_KEY", "SKILL_LLM_API_KEY", "OPENAI_API_KEY"],
            default="",
        )
        base_url, base_src = self._resolve_value(
            key="llm_api_base_url",
            env_names=["TOPHUMANWRITING_LLM_BASE_URL", "SKILL_LLM_BASE_URL", "OPENAI_BASE_URL"],
            default="https://api.openai.com/v1",
        )
        model, model_src = self._resolve_value(
            key="llm_api_model",
            env_names=["TOPHUMANWRITING_LLM_MODEL", "SKILL_LLM_MODEL", "OPENAI_MODEL"],
            default="gpt-4o-mini",
        )
        base_url = normalize_base_url(base_url)
        return {
            "base_url": base_url,
            "model": model,
            "api_key_present": bool(api_key),
            "api_key_masked": mask_secret(api_key),
            "source": {"base_url": base_src, "model": model_src, "api_key": api_key_src},
        }

    def save(self, *, base_url: str, model: str, api_key: str, save_api_key: bool) -> dict:
        base_url = normalize_base_url(base_url)
        model = (model or "").strip()
        api_key = (api_key or "").strip()

        if base_url:
            self.settings.set("llm_api_base_url", base_url)
        else:
            self.settings.set("llm_api_base_url", "")

        if model:
            self.settings.set("llm_api_model", model)
        else:
            self.settings.set("llm_api_model", "")

        if save_api_key:
            self.settings.set("llm_api_key", api_key)
        else:
            # Do not persist secrets unless explicitly requested.
            self.settings.set("llm_api_key", "")

        return self.status()

    def test(self, *, overrides: Optional[dict] = None) -> dict:
        cfg = self.resolve_config(overrides=overrides or {})
        if not cfg.api_key:
            raise RuntimeError("missing api key (set SKILL_LLM_API_KEY / OPENAI_API_KEY)")
        if not cfg.base_url_v1:
            raise RuntimeError("missing base_url")
        if not cfg.model:
            raise RuntimeError("missing model")

        client = OpenAICompatClient(cfg)
        status, resp = client.chat(
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": "Return exactly this JSON object and nothing else: {\"ok\": true}"},
            ],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
            timeout_s=30.0,
        )
        content = extract_first_content(resp)
        parsed = None
        try:
            parsed = extract_json(content)
        except Exception:
            parsed = None
        error = ""
        ok_http = bool(int(status or 0) == 200)
        ok_json = bool(isinstance(parsed, dict) and bool(parsed.get("ok")))
        if not ok_http:
            try:
                if isinstance(resp, dict):
                    if isinstance(resp.get("error", None), dict):
                        error = (resp.get("error", {}) or {}).get("message", "") or ""
                    if not error:
                        error = str(resp.get("_raw", "") or resp.get("_error", "") or "").strip()
            except Exception:
                error = ""
        elif not ok_json:
            error = "response is not strict JSON (or JSON missing ok=true)"
        if error and len(error) > 500:
            error = error[:500] + "…"

        preview = ""
        try:
            if isinstance(parsed, dict) and parsed:
                import json as _json

                preview = _json.dumps(parsed, ensure_ascii=False)[:200]
            else:
                preview = (content[:200] + "…") if len(content) > 200 else content
        except Exception:
            preview = (content[:200] + "…") if len(content) > 200 else content
        return {
            "ok": bool(ok_http and ok_json),
            "http": int(status or 0),
            "base_url": cfg.base_url_v1,
            "model": cfg.model,
            "content_preview": preview,
            "error": error,
        }


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
    settings = Settings()
    rag = RagManager(data_dir=get_settings_dir(), library_manager=library_manager, embedder=embedder)
    cite = CiteManager(data_dir=get_settings_dir(), library_manager=library_manager, embedder=embedder)
    llm_api = LLMApiManager(settings=settings)
    materials = MaterialsManager(data_dir=get_settings_dir(), library_manager=library_manager, llm_api=llm_api)
    clients = ClientRegistry()
    _syntax_lock = threading.Lock()
    _syntax_analyzer: Optional[UDPipeSyntaxAnalyzer] = None
    _syntax_error: str = ""

    def _get_syntax_analyzer() -> Optional[UDPipeSyntaxAnalyzer]:
        nonlocal _syntax_analyzer, _syntax_error
        if _syntax_analyzer is not None:
            return _syntax_analyzer
        if _syntax_error:
            return None
        with _syntax_lock:
            if _syntax_analyzer is not None:
                return _syntax_analyzer
            if _syntax_error:
                return None
            model_dir = os.path.join(get_app_dir(), "models", "syntax")
            if not os.path.exists(model_dir):
                _syntax_error = f"Missing syntax model folder: {model_dir}"
                return None
            try:
                _syntax_analyzer = UDPipeSyntaxAnalyzer(model_dir)
                return _syntax_analyzer
            except Exception as e:
                _syntax_error = str(e)
                return None

    def _resolve_pdf_import_root(library_name: str) -> str:
        slug = _library_slug(library_manager, library_name)
        slug = (slug or "").strip()
        if not slug:
            return ""
        base = os.path.join(get_settings_dir(), "pdfs", slug)
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return base

    def _resolve_library_pdf_root(library_name: str) -> str:
        library_name = (library_name or "").strip()
        if not library_name:
            return ""

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

        # Final fallback: use the import root even if no index exists yet.
        if not pdf_root:
            pdf_root = _resolve_pdf_import_root(library_name)

        return (pdf_root or "").strip()

    def _preview_cache_dir() -> str:
        base = os.path.join(get_settings_dir(), "cache", "pdf_preview")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return base

    def _preview_cache_path(kind: str, key: str, source_path: str, page: int, scale: float) -> str:
        kind = (kind or "pdf").strip().lower() or "pdf"
        key = (key or "default").strip() or "default"
        try:
            safe_key = "".join(ch for ch in key if ch.isalnum() or ch in ("-", "_", "."))
        except Exception:
            safe_key = "default"
        if not safe_key:
            safe_key = "default"

        # Invalidate cache when source file changes.
        try:
            st = os.stat(source_path)
            sig = f"{os.path.abspath(source_path)}|{int(st.st_mtime)}|{int(st.st_size)}|{int(page)}|{float(scale):.3f}"
        except Exception:
            sig = f"{os.path.abspath(source_path)}|{int(page)}|{float(scale):.3f}"
        h = hashlib.sha1(sig.encode("utf-8", errors="ignore")).hexdigest()[:18]
        s100 = int(max(50, min(300, round(float(scale) * 100))))

        d = os.path.join(_preview_cache_dir(), kind, safe_key)
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, f"p{int(page)}_s{s100}_{h}.png")

    def _ensure_pdf_page_png(source_path: str, page: int, scale: float, cache_path: str) -> str:
        if cache_path and os.path.exists(cache_path):
            return cache_path

        # Render to a temp file then atomically replace to avoid partial writes.
        tmp = f"{cache_path}.tmp_{secrets.token_hex(4)}"
        try:
            import fitz  # type: ignore

            doc = fitz.open(source_path)
            try:
                idx = int(page) - 1
                if idx < 0 or idx >= doc.page_count:
                    raise HTTPException(status_code=404, detail=f"page out of range: {page}")
                mat = fitz.Matrix(float(scale), float(scale))
                pix = doc[idx].get_pixmap(matrix=mat, alpha=False)
                data = pix.tobytes("png")
            finally:
                try:
                    doc.close()
                except Exception:
                    pass

            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, cache_path)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

        return cache_path

    def _citecheck_root() -> str:
        base = os.path.join(get_settings_dir(), "citecheck")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return base

    def _citecheck_drafts_dir() -> str:
        d = os.path.join(_citecheck_root(), "drafts")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _citecheck_jobs_dir() -> str:
        d = os.path.join(_citecheck_root(), "jobs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _audit_root() -> str:
        base = os.path.join(get_settings_dir(), "audit")
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            pass
        return base

    def _audit_papers_dir() -> str:
        d = os.path.join(_audit_root(), "papers")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _audit_jobs_dir() -> str:
        d = os.path.join(_audit_root(), "jobs")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _audit_coverage_dir() -> str:
        d = os.path.join(_audit_root(), "coverage")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return d

    def _pdf_tree_sig(root: str) -> dict:
        root = os.path.abspath(str(root or ""))
        if not root or not os.path.exists(root):
            return {"root": root, "pdf_count": 0, "pdf_max_mtime": 0}
        count = 0
        max_mtime = 0
        try:
            for dp, _dns, fns in os.walk(root):
                for fn in fns:
                    if not str(fn).lower().endswith(".pdf"):
                        continue
                    count += 1
                    full = os.path.join(dp, fn)
                    try:
                        mt = int(os.path.getmtime(full) or 0)
                    except Exception:
                        mt = 0
                    if mt > max_mtime:
                        max_mtime = mt
        except Exception:
            pass
        return {"root": root, "pdf_count": int(count), "pdf_max_mtime": int(max_mtime)}

    def _audit_load_paper(paper_id: str) -> dict:
        paper_id = (paper_id or "").strip()
        if not paper_id:
            return {}
        meta_path = os.path.join(_audit_papers_dir(), f"{paper_id}.json")
        if not os.path.exists(meta_path):
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return {}
        if not isinstance(meta, dict):
            return {}
        pdf_path = str(meta.get("path", "") or "").strip()
        if not pdf_path or not os.path.exists(pdf_path):
            return {}
        meta["path"] = pdf_path
        meta["id"] = paper_id
        return meta

    def _citecheck_load_draft(draft_id: str) -> dict:
        draft_id = (draft_id or "").strip()
        if not draft_id:
            return {}
        meta_path = os.path.join(_citecheck_drafts_dir(), f"{draft_id}.json")
        if not os.path.exists(meta_path):
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            return {}
        if not isinstance(meta, dict):
            return {}
        pdf_path = str(meta.get("path", "") or "").strip()
        if not pdf_path or not os.path.exists(pdf_path):
            return {}
        meta["path"] = pdf_path
        meta["id"] = draft_id
        return meta

    def _safe_rel_path(raw_name: str) -> str:
        name = (raw_name or "").replace("\\", "/").strip()
        # Keep only relative paths
        while name.startswith("/"):
            name = name[1:]
        name = name.replace(":", "")
        parts = []
        for part in name.split("/"):
            part = (part or "").strip()
            if not part or part in (".", ".."):
                continue
            # Avoid weird control chars
            part = "".join(ch for ch in part if ch >= " " and ch not in "<>:\"|?*")
            if not part:
                continue
            parts.append(part)
        return "/".join(parts)

    try:
        app.state.tasks = tasks
        app.state.llm_api = llm_api
        app.state.settings = settings
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
            armed_at: Optional[float] = None
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

    def _count_pdfs(root: str) -> int:
        if not root or not os.path.exists(root):
            return 0
        count = 0
        try:
            for _, _, files in os.walk(root):
                for f in files:
                    if str(f).lower().endswith(".pdf"):
                        count += 1
        except Exception:
            return 0
        return int(count)

    def _read_json(path: str) -> dict:
        try:
            if path and os.path.exists(path):
                import json as _json

                with open(path, "r", encoding="utf-8") as f:
                    d = _json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            return {}
        return {}

    @app.get("/api/libraries/summary")
    def libraries_summary():
        libs = library_manager.list_libraries()
        out = []
        for it in libs or []:
            name = (it.get("name", "") if isinstance(it, dict) else "") or ""
            name = str(name).strip()
            if not name:
                continue

            pdf_import_root = _resolve_pdf_import_root(name)
            pdf_import_count = _count_pdfs(pdf_import_root)

            rag_ok = rag.index_ready(name)
            cite_ok = cite.index_ready(name)

            rag_manifest = {}
            rag_pdf_root = ""
            rag_pdf_count = 0
            rag_node_count = 0
            rag_built_at = 0
            rag_built_at_iso = ""
            if rag_ok:
                try:
                    ix = rag._indexer(name)  # type: ignore[attr-defined]
                    rag_manifest = _read_json(getattr(ix, "manifest_path", "") or "")
                    rag_pdf_root = str(rag_manifest.get("pdf_root", "") or "").strip()
                    try:
                        rag_pdf_count = int(rag_manifest.get("pdf_count", 0) or 0)
                    except Exception:
                        rag_pdf_count = 0
                    try:
                        rag_node_count = int(rag_manifest.get("node_count", 0) or 0)
                    except Exception:
                        rag_node_count = 0
                    try:
                        rag_built_at = int(rag_manifest.get("built_at", 0) or 0)
                    except Exception:
                        rag_built_at = 0
                    if rag_built_at > 0:
                        try:
                            rag_built_at_iso = datetime.fromtimestamp(rag_built_at).isoformat(sep=" ", timespec="seconds")
                        except Exception:
                            rag_built_at_iso = ""
                except Exception:
                    rag_manifest = {}

            out.append(
                {
                    **(it if isinstance(it, dict) else {}),
                    "name": name,
                    "pdf_import_root": pdf_import_root,
                    "pdf_import_count": int(pdf_import_count),
                    "rag_index": bool(rag_ok),
                    "cite_index": bool(cite_ok),
                    "rag_manifest": rag_manifest,
                    "rag_pdf_root": rag_pdf_root,
                    "rag_pdf_count": int(rag_pdf_count),
                    "rag_node_count": int(rag_node_count),
                    "rag_built_at": int(rag_built_at),
                    "rag_built_at_iso": rag_built_at_iso,
                }
            )
        return {"libraries": out}

    @app.post("/api/libraries")
    def create_library(payload: dict = Body(...)):
        name = (payload.get("name", "") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name required")
        raw_kind = (payload.get("kind", "") or "").strip().lower()
        kind = raw_kind if raw_kind in ("exemplar", "references") else "exemplar"
        if library_manager.library_exists(name):
            raise HTTPException(status_code=409, detail="library exists")
        path = library_manager.create_library(name, kind=kind)
        return {"name": name, "path": path, "kind": kind}

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
        return llm_api.status()

    @app.get("/api/llm/api/status")
    def llm_api_status():
        return llm_api.status()

    @app.post("/api/llm/api/save")
    def llm_api_save(payload: dict = Body(default={})):
        base_url = (payload.get("base_url", "") or "").strip()
        model = (payload.get("model", "") or "").strip()
        api_key = (payload.get("api_key", "") or "").strip()
        save_api_key = bool(payload.get("save_api_key", False))
        try:
            return llm_api.save(base_url=base_url, model=model, api_key=api_key, save_api_key=save_api_key)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/api/test")
    def llm_api_test(payload: dict = Body(default={})):
        try:
            return llm_api.test(overrides=payload or {})
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/library/build")
    def build_library(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_folder = (payload.get("folder", "") or "").strip()
        with_syntax = bool(payload.get("with_syntax", True))
        with_materials = bool(payload.get("with_materials", True))
        materials_use_llm = bool(payload.get("materials_use_llm", False))
        materials_llm_overrides = payload.get("materials_llm_overrides", None)
        with_cite = bool(payload.get("with_cite", True))
        try:
            materials_max_pdfs = payload.get("materials_max_pdfs", None)
            materials_max_pdfs = int(materials_max_pdfs) if materials_max_pdfs is not None else None
        except Exception:
            materials_max_pdfs = None
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_folder:
            pdf_folder = _resolve_pdf_import_root(library_name)
        if not pdf_folder or not os.path.exists(pdf_folder):
            raise HTTPException(status_code=400, detail="folder not found (import PDFs first, or paste a valid path)")

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        def worker():
            try:
                lib_path = library_manager.get_library_path(library_name)
                corpus = AcademicCorpus(lib_path)
                lib_kind = "exemplar"
                try:
                    info0 = library_manager.get_library_info(library_name) or {}
                    lib_kind = str(info0.get("kind", "") or "").strip().lower() or "exemplar"
                except Exception:
                    lib_kind = "exemplar"

                def pdf_progress(done: int, total: int, detail: str = ""):
                    tasks.set_progress(ts.id, stage="pdf_extract", done=done, total=total, detail=detail)

                def semantic_progress(done: int, total: int, detail: str = ""):
                    tasks.set_progress(ts.id, stage="semantic_embed", done=done, total=total, detail=str(detail or ""))

                def syntax_progress(done: int, total: int, detail: str = ""):
                    tasks.set_progress(ts.id, stage="syntax", done=done, total=total, detail=str(detail or ""))

                syntax_analyzer = _get_syntax_analyzer() if with_syntax else None

                count = corpus.process_pdf_folder(
                    pdf_folder,
                    pdf_progress,
                    semantic_embedder=embedder,
                    semantic_progress_callback=semantic_progress,
                    syntax_analyzer=syntax_analyzer,
                    syntax_progress_callback=syntax_progress if syntax_analyzer is not None else None,
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

                if with_cite and lib_kind == "exemplar":
                    cite.build_index(
                        library_name=library_name,
                        pdf_folder=pdf_folder,
                        task_id=ts.id,
                        tasks=tasks,
                        cancel_event=cancel_event,
                        max_pages=None,
                    )

                if with_materials and lib_kind == "exemplar":
                    materials.build_index(
                        library_name=library_name,
                        pdf_root=pdf_folder,
                        task_id=ts.id,
                        tasks=tasks,
                        cancel_event=cancel_event,
                        use_llm=materials_use_llm,
                        llm_overrides=materials_llm_overrides if isinstance(materials_llm_overrides, dict) else None,
                        max_pdfs=materials_max_pdfs,
                    )

                corpus.save_vocabulary()
                tasks.finish(ts.id, status="done", error="")
            except RagIndexError as e:
                tasks.finish(ts.id, status="failed", error=str(e))
            except Exception:
                tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.get("/api/library/pdf_root")
    def library_pdf_root(library: str):
        library = (library or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        root = _resolve_pdf_import_root(library)
        count = 0
        try:
            for dp, _, files in os.walk(root):
                for f in files:
                    if str(f).lower().endswith(".pdf"):
                        count += 1
        except Exception:
            count = 0
        return {"library": library, "pdf_root": root, "pdf_count": count}

    @app.post("/api/library/import/clear")
    def library_import_clear(payload: dict = Body(...)):
        library = (payload.get("library", "") or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        root = _resolve_pdf_import_root(library)
        try:
            if os.path.exists(root):
                # Only delete within the import root
                import shutil

                shutil.rmtree(root)
            os.makedirs(root, exist_ok=True)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "pdf_root": root}

    @app.post("/api/library/upload_pdf")
    async def library_upload_pdf(
        library: str = Form(...),
        file: UploadFile = File(...),
        overwrite: int = Form(0),
    ):
        library = (library or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        if file is None:
            raise HTTPException(status_code=400, detail="file required")
        filename = _safe_rel_path(getattr(file, "filename", "") or "")
        if not filename:
            raise HTTPException(status_code=400, detail="bad filename")
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="only .pdf allowed")

        root = _resolve_pdf_import_root(library)
        rel = filename.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(root, rel))
        try:
            root_abs = os.path.abspath(root).lower()
            full_abs = os.path.abspath(full).lower()
            if not (full_abs == root_abs or full_abs.startswith(root_abs + os.sep.lower())):
                raise HTTPException(status_code=400, detail="invalid path")
        except HTTPException:
            raise
        except Exception:
            pass

        # Ensure parent directory exists
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
        except Exception:
            pass

        if os.path.exists(full) and not bool(int(overwrite or 0)):
            return {"ok": True, "skipped": True, "pdf_root": root, "rel": rel}

        try:
            with open(full, "wb") as f:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True, "skipped": False, "pdf_root": root, "rel": rel}

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
        has_mat = materials.index_ready(library)
        return {
            "library": library,
            "semantic_index": has_sem,
            "rag_index": has_rag,
            "cite_index": has_cite,
            "materials_index": has_mat,
            "info": info,
        }

    @app.get("/api/materials/status")
    def materials_status(library: str):
        library = (library or "").strip()
        if not library:
            raise HTTPException(status_code=400, detail="library required")
        try:
            return {
                "library": library,
                "materials_index": materials.index_ready(library),
                "manifest": materials.load_manifest(library),
            }
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/materials/build")
    def materials_build(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_root = (payload.get("pdf_root", "") or payload.get("folder", "") or "").strip()
        use_llm = bool(payload.get("use_llm", False))
        llm_overrides = payload.get("llm_overrides", None)
        try:
            max_pdfs = payload.get("max_pdfs", None)
            max_pdfs = int(max_pdfs) if max_pdfs is not None else None
        except Exception:
            max_pdfs = None

        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_root:
            pdf_root = _resolve_library_pdf_root(library_name)
        if not pdf_root or not os.path.exists(pdf_root):
            raise HTTPException(status_code=400, detail="pdf_root not found (import PDFs first)")

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        def worker():
            try:
                materials.build_index(
                    library_name=library_name,
                    pdf_root=pdf_root,
                    task_id=ts.id,
                    tasks=tasks,
                    cancel_event=cancel_event,
                    use_llm=use_llm,
                    llm_overrides=llm_overrides if isinstance(llm_overrides, dict) else None,
                    max_pdfs=max_pdfs,
                )
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return
                tasks.finish(ts.id, status="done", error="")
            except Exception:
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                else:
                    tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

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

    @app.post("/api/norms/citecheck/upload_main_pdf")
    async def citecheck_upload_main_pdf(file: UploadFile = File(...)):
        if file is None:
            raise HTTPException(status_code=400, detail="file required")
        name = _safe_rel_path(getattr(file, "filename", "") or "")
        base = os.path.basename(name) if name else "main.pdf"
        if not base.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="only .pdf allowed")

        draft_id = f"d{int(time.time() * 1000)}_{secrets.token_hex(4)}"
        drafts_dir = _citecheck_drafts_dir()
        pdf_path = os.path.join(drafts_dir, f"{draft_id}.pdf")
        meta_path = os.path.join(drafts_dir, f"{draft_id}.json")

        try:
            with open(pdf_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        try:
            meta = {
                "id": draft_id,
                "filename": base,
                "path": pdf_path,
                "created_at": int(time.time()),
                "size": int(os.path.getsize(pdf_path)) if os.path.exists(pdf_path) else 0,
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            meta = {"id": draft_id, "filename": base, "path": pdf_path, "created_at": int(time.time())}

        return {"ok": True, "draft": meta}

    @app.get("/api/norms/citecheck/drafts")
    def citecheck_list_drafts(limit: int = 20):
        limit = max(1, min(int(limit or 20), 100))
        drafts_dir = _citecheck_drafts_dir()
        metas = []
        try:
            for p in os.listdir(drafts_dir):
                if not p.endswith(".json"):
                    continue
                full = os.path.join(drafts_dir, p)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        m = json.load(f)
                    if not isinstance(m, dict):
                        continue
                    did = str(m.get("id", "") or "").strip() or os.path.splitext(p)[0]
                    fname = str(m.get("filename", "") or "").strip() or did
                    created_at = int(m.get("created_at", 0) or 0)
                    path = str(m.get("path", "") or "").strip()
                    if not path or not os.path.exists(path):
                        continue
                    metas.append({"id": did, "filename": fname, "created_at": created_at})
                except Exception:
                    continue
        except Exception:
            metas = []
        metas.sort(key=lambda x: int(x.get("created_at", 0) or 0), reverse=True)
        return {"drafts": metas[:limit]}

    @app.post("/api/norms/citecheck/open_draft")
    def citecheck_open_draft(payload: dict = Body(...)):
        draft_id = (payload.get("draft_id", "") or "").strip()
        meta = _citecheck_load_draft(draft_id)
        if not meta:
            raise HTTPException(status_code=404, detail="draft not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True}

    @app.get("/api/norms/citecheck/draft_pdf")
    def citecheck_get_draft_pdf(draft_id: str):
        did = (draft_id or "").strip()
        meta = _citecheck_load_draft(did)
        if not meta:
            raise HTTPException(status_code=404, detail="draft not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=os.path.basename(path),
            content_disposition_type="inline",
        )

    @app.get("/api/norms/citecheck/draft_page.png")
    def citecheck_get_draft_page_png(draft_id: str, page: int = 1, scale: float = 1.6):
        did = (draft_id or "").strip()
        if not did:
            raise HTTPException(status_code=400, detail="draft_id required")
        meta = _citecheck_load_draft(did)
        if not meta:
            raise HTTPException(status_code=404, detail="draft not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")

        page_i = int(page or 1)
        if page_i <= 0:
            page_i = 1
        scale_f = float(scale or 1.6)
        if not (0.75 <= scale_f <= 2.5):
            scale_f = 1.6

        cache_path = _preview_cache_path("draft", did, path, page_i, scale_f)
        png_path = _ensure_pdf_page_png(path, page_i, scale_f, cache_path)
        return FileResponse(
            png_path,
            media_type="image/png",
            filename=os.path.basename(png_path),
            content_disposition_type="inline",
        )

    @app.post("/api/norms/citecheck/run")
    def citecheck_run(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        draft_id = (payload.get("draft_id", "") or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        meta = _citecheck_load_draft(draft_id)
        if not meta:
            raise HTTPException(status_code=404, detail="draft not found (upload main pdf first)")

        pdf_root = _resolve_library_pdf_root(library_name)
        if not pdf_root or not os.path.exists(pdf_root):
            raise HTTPException(status_code=400, detail="library pdf root missing (import PDFs first)")

        use_llm = bool(payload.get("use_llm", True))
        llm_client = None
        if use_llm:
            try:
                api_cfg = llm_api.resolve_config(
                    overrides={
                        "api_key": payload.get("api_key", ""),
                        "base_url": payload.get("base_url", ""),
                        "model": payload.get("model", ""),
                    }
                )
            except Exception as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not api_cfg.api_key:
                raise HTTPException(status_code=400, detail="missing api key (set SKILL_LLM_API_KEY / OPENAI_API_KEY)")
            if not api_cfg.base_url_v1:
                raise HTTPException(status_code=400, detail="missing base_url (set SKILL_LLM_BASE_URL / OPENAI_BASE_URL)")
            if not api_cfg.model:
                raise HTTPException(status_code=400, detail="missing model (set SKILL_LLM_MODEL / OPENAI_MODEL)")
            llm_client = OpenAICompatClient(api_cfg)

        cfg = CiteCheckConfig(
            title_match_threshold=float(payload.get("title_match_threshold", 0.55) or 0.55),
            paragraph_top_k=int(payload.get("paragraph_top_k", 5) or 5),
            max_pairs=int(payload.get("max_pairs", 80) or 80),
            use_llm=bool(use_llm and llm_client is not None),
            llm_timeout_s=float(payload.get("timeout_s", 90.0) or 90.0),
        )

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        main_pdf_path = str(meta.get("path", "") or "").strip()
        jobs_dir = _citecheck_jobs_dir()
        out_path = os.path.join(jobs_dir, f"{ts.id}.json")

        def worker():
            try:
                def embed_texts(texts):
                    n = 0
                    try:
                        n = int(len(texts or []))
                    except Exception:
                        n = 0
                    # Avoid spamming the task stage for tiny 1-off embeddings (e.g. query vectors),
                    # otherwise the UI looks "stuck" on citecheck_embed while waiting for LLM.
                    progress_cb = None
                    if n >= 20:
                        def _progress(d, t):
                            tasks.set_progress(
                                ts.id,
                                stage="citecheck_embed",
                                done=int(d or 0),
                                total=int(t or 0),
                                detail="构建向量…",
                            )

                        progress_cb = _progress
                    return embedder.embed(
                        texts,
                        batch_size=SEMANTIC_EMBED_BATCH,
                        progress_callback=progress_cb,
                        progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                        cancel_event=cancel_event,
                    )

                runner = CiteCheckRunner(
                    data_dir=get_settings_dir(),
                    embed_texts=embed_texts,
                    model_fingerprint=embedder.model_fingerprint(),
                )

                def prog(stage: str, done: int, total: int, detail: str):
                    st = str(stage or "").strip().lower()
                    tasks.set_progress(ts.id, stage=f"citecheck_{st}", done=done, total=total, detail=detail)

                result = runner.run(
                    main_pdf_path=main_pdf_path,
                    papers_root=pdf_root,
                    library_pdf_root=pdf_root,
                    cfg=cfg,
                    llm=llm_client,
                    cancel_cb=cancel_event.is_set,
                    progress_cb=prog,
                )
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return
                try:
                    if isinstance(result, dict):
                        meta2 = result.get("meta", {})
                        if isinstance(meta2, dict):
                            meta2["library"] = library_name
                            meta2["draft_id"] = draft_id
                            meta2["main_pdf_path"] = main_pdf_path
                except Exception:
                    pass
                try:
                    tmp = out_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, out_path)
                except Exception:
                    pass
                tasks.finish(ts.id, status="done", error="")
            except Exception:
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                else:
                    tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.get("/api/norms/citecheck/result")
    def citecheck_result(task_id: str):
        task_id = (task_id or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        path = os.path.join(_citecheck_jobs_dir(), f"{task_id}.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="result not found")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {}

    @app.post("/api/norms/citecheck/missing/run")
    def citecheck_missing_run(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        draft_id = (payload.get("draft_id", "") or "").strip()
        only_cited = bool(payload.get("only_cited", True))
        with_oa = bool(payload.get("with_oa", False))
        download_oa = bool(payload.get("download_oa", False))
        if download_oa and not with_oa:
            raise HTTPException(status_code=400, detail="download_oa requires with_oa=true")
        try:
            max_items = int(payload.get("limit", 60) or 60)
        except Exception:
            max_items = 60
        max_items = max(5, min(int(max_items or 0), 500))

        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        meta = _citecheck_load_draft(draft_id)
        if not meta:
            raise HTTPException(status_code=404, detail="draft not found")

        pdf_root = _resolve_pdf_import_root(library_name)
        if not pdf_root:
            raise HTTPException(status_code=400, detail="library pdf root not found")

        cfg = CiteCheckConfig(title_match_threshold=float(payload.get("title_match_threshold", 0.55) or 0.55), use_llm=False)

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        main_pdf_path = str(meta.get("path", "") or "").strip()
        jobs_dir = _citecheck_jobs_dir()
        out_path = os.path.join(jobs_dir, f"{ts.id}.missing.json")

        def worker():
            try:
                def embed_texts(texts):
                    n = 0
                    try:
                        n = int(len(texts or []))
                    except Exception:
                        n = 0

                    progress_cb = None
                    if n >= 20:
                        def _progress(d, t):
                            tasks.set_progress(
                                ts.id,
                                stage="citecheck_embed",
                                done=int(d or 0),
                                total=int(t or 0),
                                detail="构建匹配向量…",
                            )

                        progress_cb = _progress
                    return embedder.embed(
                        texts,
                        batch_size=SEMANTIC_EMBED_BATCH,
                        progress_callback=progress_cb,
                        progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                        cancel_event=cancel_event,
                    )

                runner = CiteCheckRunner(
                    data_dir=get_settings_dir(),
                    embed_texts=embed_texts,
                    model_fingerprint=embedder.model_fingerprint(),
                )

                def prog(stage: str, done: int, total: int, detail: str):
                    st = str(stage or "").strip().lower()
                    # Reuse existing stage label for indexing.
                    if st == "papers_index":
                        tasks.set_progress(ts.id, stage="citecheck_papers_index", done=done, total=total, detail=detail)
                    elif st == "missing_scan":
                        tasks.set_progress(ts.id, stage="citecheck_missing_scan", done=done, total=total, detail=detail)
                    else:
                        tasks.set_progress(ts.id, stage=f"citecheck_missing_{st}", done=done, total=total, detail=detail)

                result = runner.find_missing_papers(
                    main_pdf_path=main_pdf_path,
                    papers_root=pdf_root,
                    cfg=cfg,
                    only_cited=only_cited,
                    max_items=max_items,
                    cancel_cb=cancel_event.is_set,
                    progress_cb=prog,
                )

                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return

                # Optional online enrichment (合法：只做 DOI/OA 线索，不提供 Sci-Hub 等来源)。
                missing = result.get("missing", []) if isinstance(result, dict) else []
                if with_oa and isinstance(missing, list) and missing:
                    for i, it in enumerate(missing, start=1):
                        if cancel_event.is_set():
                            break
                        try:
                            author = str(it.get("cited_author", "") or "").strip()
                            year = str(it.get("cited_year", "") or "").strip()
                            title = str(it.get("ref_title", "") or "").strip()
                            query = (title or f"{author} {year}").strip()
                            tasks.set_progress(
                                ts.id,
                                stage="citecheck_missing_oa",
                                done=i,
                                total=len(missing),
                                detail=(query[:80] + ("…" if len(query) > 80 else "")),
                            )

                            sem = semantic_scholar_search(query, limit=3, timeout_s=20.0)
                            cr = crossref_search(query, rows=3, timeout_s=20.0)
                            best = pick_best_oa_candidate(semantic_items=sem, crossref_items=cr, target_year=year)

                            it["doi"] = str(best.get("doi", "") or "").strip()
                            it["oa_pdf_url"] = str(best.get("oa_pdf_url", "") or "").strip()
                            it["landing_url"] = str(best.get("landing_url", "") or "").strip()
                            it["oa_title"] = str(best.get("title", "") or "").strip()
                            it["oa_year"] = best.get("year", "") or ""
                            it["open_access"] = bool(best.get("is_open_access", False))
                            it["oa_source"] = str(best.get("source", "") or "").strip()
                        except Exception:
                            continue

                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return

                if download_oa and isinstance(missing, list) and missing:
                    save_dir = os.path.join(pdf_root, "oa_downloads")
                    try:
                        os.makedirs(save_dir, exist_ok=True)
                    except Exception:
                        pass
                    dl_total = sum(1 for x in missing if str((x or {}).get("oa_pdf_url", "") or "").strip())
                    dl_done = 0
                    for it in missing:
                        if cancel_event.is_set():
                            break
                        oa_url = str((it or {}).get("oa_pdf_url", "") or "").strip()
                        if not oa_url:
                            continue
                        dl_done += 1
                        author = str(it.get("cited_author", "") or "").strip()
                        year = str(it.get("cited_year", "") or "").strip()
                        title = str(it.get("oa_title", "") or it.get("ref_title", "") or "").strip()
                        base = sanitize_filename(f"{author} ({year}) - {title}" if title else f"{author} ({year})")
                        filename = base + ".pdf"
                        dest = os.path.join(save_dir, filename)
                        if os.path.exists(dest):
                            for k in range(2, 99):
                                dest2 = os.path.join(save_dir, f"{base}_{k}.pdf")
                                if not os.path.exists(dest2):
                                    dest = dest2
                                    break
                        tasks.set_progress(ts.id, stage="citecheck_missing_download", done=dl_done, total=dl_total, detail=os.path.basename(dest))
                        ok = download_oa_pdf(oa_url, dest, timeout_s=80.0)
                        it["downloaded"] = bool(ok)
                        it["download_rel"] = os.path.relpath(dest, pdf_root).replace("\\", "/") if ok else ""

                try:
                    if isinstance(result, dict):
                        meta2 = result.get("meta", {})
                        if isinstance(meta2, dict):
                            meta2["library"] = library_name
                            meta2["draft_id"] = draft_id
                            meta2["main_pdf_path"] = main_pdf_path
                            meta2["with_oa"] = bool(with_oa)
                            meta2["download_oa"] = bool(download_oa)
                except Exception:
                    pass

                try:
                    tmp = out_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, out_path)
                except Exception:
                    pass
                tasks.finish(ts.id, status="done", error="")
            except Exception:
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                else:
                    tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.get("/api/norms/citecheck/missing/result")
    def citecheck_missing_result(task_id: str):
        task_id = (task_id or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        path = os.path.join(_citecheck_jobs_dir(), f"{task_id}.missing.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="result not found")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {}

    @app.post("/api/audit/upload_paper_pdf")
    async def audit_upload_paper_pdf(file: UploadFile = File(...)):
        if file is None:
            raise HTTPException(status_code=400, detail="file required")
        name = _safe_rel_path(getattr(file, "filename", "") or "")
        base = os.path.basename(name) if name else "paper.pdf"
        if not base.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="only .pdf allowed")

        paper_id = f"p{int(time.time() * 1000)}_{secrets.token_hex(4)}"
        papers_dir = _audit_papers_dir()
        pdf_path = os.path.join(papers_dir, f"{paper_id}.pdf")
        meta_path = os.path.join(papers_dir, f"{paper_id}.json")

        try:
            with open(pdf_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        meta = {
            "id": paper_id,
            "filename": base,
            "path": pdf_path,
            "created_at": int(time.time()),
            "size": int(os.path.getsize(pdf_path)) if os.path.exists(pdf_path) else 0,
        }
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return {"ok": True, "paper": meta}

    @app.get("/api/audit/papers")
    def audit_list_papers(limit: int = 30):
        limit = max(1, min(int(limit or 30), 200))
        papers_dir = _audit_papers_dir()
        metas = []
        try:
            for p in os.listdir(papers_dir):
                if not p.endswith(".json"):
                    continue
                full = os.path.join(papers_dir, p)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        m = json.load(f)
                    if not isinstance(m, dict):
                        continue
                    pid = str(m.get("id", "") or "").strip() or os.path.splitext(p)[0]
                    fname = str(m.get("filename", "") or "").strip() or pid
                    created_at = int(m.get("created_at", 0) or 0)
                    path = str(m.get("path", "") or "").strip()
                    if not path or not os.path.exists(path):
                        continue
                    metas.append({"id": pid, "filename": fname, "created_at": created_at})
                except Exception:
                    continue
        except Exception:
            metas = []
        metas.sort(key=lambda x: int(x.get("created_at", 0) or 0), reverse=True)
        return {"papers": metas[:limit]}

    @app.post("/api/audit/open_paper")
    def audit_open_paper(payload: dict = Body(...)):
        paper_id = (payload.get("paper_id", "") or "").strip()
        meta = _audit_load_paper(paper_id)
        if not meta:
            raise HTTPException(status_code=404, detail="paper not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"ok": True}

    @app.get("/api/audit/paper_pdf")
    def audit_get_paper_pdf(paper_id: str):
        pid = (paper_id or "").strip()
        meta = _audit_load_paper(pid)
        if not meta:
            raise HTTPException(status_code=404, detail="paper not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=os.path.basename(path),
            content_disposition_type="inline",
        )

    @app.get("/api/audit/paper_page.png")
    def audit_get_paper_page_png(paper_id: str, page: int = 1, scale: float = 1.6):
        pid = (paper_id or "").strip()
        if not pid:
            raise HTTPException(status_code=400, detail="paper_id required")
        meta = _audit_load_paper(pid)
        if not meta:
            raise HTTPException(status_code=404, detail="paper not found")
        path = str(meta.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(status_code=404, detail="file not found")

        page_i = int(page or 1)
        if page_i <= 0:
            page_i = 1
        scale_f = float(scale or 1.6)
        if not (0.75 <= scale_f <= 2.5):
            scale_f = 1.6

        cache_path = _preview_cache_path("audit_paper", pid, path, page_i, scale_f)
        png_path = _ensure_pdf_page_png(path, page_i, scale_f, cache_path)
        return FileResponse(
            png_path,
            media_type="image/png",
            filename=os.path.basename(png_path),
            content_disposition_type="inline",
        )

    @app.post("/api/audit/run")
    def audit_run(payload: dict = Body(...)):
        exemplar_library = (payload.get("exemplar_library", "") or payload.get("library", "") or "").strip()
        references_library = (payload.get("references_library", "") or "").strip()
        paper_id = (payload.get("paper_id", "") or "").strip()
        series_id = (payload.get("series_id", "") or "").strip() or paper_id
        include_citecheck = bool(payload.get("include_citecheck", True))
        use_llm = bool(payload.get("use_llm", True))
        use_llm_review = bool(payload.get("use_llm_review", True))
        # Budget (recommended): token budget per full audit run (LLM review + citecheck).
        try:
            max_llm_tokens = int(payload.get("max_llm_tokens", payload.get("max_total_tokens", 200_000)) or 200_000)
        except Exception:
            max_llm_tokens = 200_000
        if max_llm_tokens < 0:
            max_llm_tokens = 0
        if max_llm_tokens > 10_000_000:
            max_llm_tokens = 10_000_000

        # Optional cost estimate (unitless). Keep legacy keys for compatibility.
        try:
            cost_per_1m_tokens = float(payload.get("cost_per_1m_tokens", payload.get("cost_per_1m_tokens_rmb", 0.0)) or 0.0)
        except Exception:
            cost_per_1m_tokens = 0.0
        try:
            max_cost = float(payload.get("max_cost", payload.get("max_cost_rmb", 0.0)) or 0.0)
        except Exception:
            max_cost = 0.0

        if not paper_id:
            raise HTTPException(status_code=400, detail="paper_id required")
        if not exemplar_library:
            raise HTTPException(status_code=400, detail="exemplar_library required")
        meta = _audit_load_paper(paper_id)
        if not meta:
            raise HTTPException(status_code=404, detail="paper not found (upload paper pdf first)")
        paper_path = str(meta.get("path", "") or "").strip()
        if not paper_path or not os.path.exists(paper_path):
            raise HTTPException(status_code=404, detail="paper file not found")
        if not rag.index_ready(exemplar_library):
            raise HTTPException(status_code=400, detail="rag index missing (build exemplar library first)")

        pdf_root = ""
        if include_citecheck:
            if not references_library:
                references_library = exemplar_library
        if include_citecheck and references_library:
            pdf_root = _resolve_library_pdf_root(references_library)
            if not pdf_root or not os.path.exists(pdf_root):
                raise HTTPException(status_code=400, detail="references library pdf root missing (import PDFs first)")

        try:
            top_k = int(payload.get("top_k", 20) or 20)
        except Exception:
            top_k = 20
        try:
            max_sentences = int(payload.get("max_sentences", 3600) or 3600)
        except Exception:
            max_sentences = 3600
        try:
            min_sentence_len = int(payload.get("min_sentence_len", 20) or 20)
        except Exception:
            min_sentence_len = 20
        try:
            low_thr = float(payload.get("low_alignment_threshold", 0.35) or 0.35)
        except Exception:
            low_thr = 0.35
        max_pages = payload.get("max_pages", None)
        try:
            max_pages = int(max_pages) if max_pages is not None else None
        except Exception:
            max_pages = None

        ts = tasks.create()
        cancel_event = tasks.cancel_event(ts.id)
        assert cancel_event is not None

        out_path = os.path.join(_audit_jobs_dir(), f"{ts.id}.json")

        def worker():
            try:
                # Load exemplar corpus stats (for style baseline / syntax outlier comparisons).
                corpus = None
                try:
                    lib_path = library_manager.get_library_path(exemplar_library)
                    corpus = AcademicCorpus(lib_path)
                    corpus.load_vocabulary()
                except Exception:
                    corpus = None

                syntax_analyzer = _get_syntax_analyzer()

                def search_exemplars(query: str, k: int):
                    rs = rag.search(library_name=exemplar_library, query=query, top_k=k)
                    out = []
                    for sc, node in (rs or [])[: max(1, int(k or 0))]:
                        try:
                            pdf = getattr(node, "pdf", "") or ""
                            page = int(getattr(node, "page", 0) or 0)
                            txt = (getattr(node, "text", "") or "").strip()
                        except Exception:
                            pdf, page, txt = "", 0, ""
                        out.append((float(sc or 0.0), {"pdf": pdf, "page": page, "text": txt}))
                    return out

                def prog(stage: str, done: int, total: int, detail: str):
                    tasks.set_progress(ts.id, stage=str(stage or ""), done=int(done or 0), total=int(total or 0), detail=str(detail or ""))

                result = run_full_paper_audit(
                    paper_pdf_path=paper_path,
                    exemplar_library=exemplar_library,
                    search_exemplars=search_exemplars,
                    corpus=corpus,
                    syntax_analyzer=syntax_analyzer,
                    max_pages=max_pages,
                    max_sentences=max_sentences,
                    min_sentence_len=min_sentence_len,
                    top_k=top_k,
                    low_alignment_threshold=low_thr,
                    include_style=True,
                    include_repetition=True,
                    include_syntax=True,
                    cancel_cb=cancel_event.is_set,
                    progress_cb=prog,
                )
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                    return

                try:
                    if isinstance(result, dict):
                        meta2 = result.get("meta", {})
                        if isinstance(meta2, dict):
                            meta2["paper_id"] = paper_id
                            meta2["series_id"] = series_id
                            meta2["paper_filename"] = str(meta.get("filename", "") or "")
                except Exception:
                    pass

                coverage = None
                try:
                    coverage = ReviewCoverageStore.load_or_create(dir_path=_audit_coverage_dir(), series_id=series_id)
                    # If exemplar library changes, reset style-alignment coverage to avoid hiding results.
                    if coverage.get_context("exemplar_library") != exemplar_library:
                        for cat in ("sentence_alignment", "paragraph_alignment", "citation_style", "outline"):
                            coverage.clear_category(cat)
                        coverage.set_context("exemplar_library", exemplar_library)

                    # If citecheck library changes, reset citecheck coverage.
                    if include_citecheck and pdf_root:
                        sig = _pdf_tree_sig(pdf_root)
                        if coverage.get_context("citecheck_papers_sig") != sig:
                            coverage.clear_category("citecheck")
                            coverage.set_context("citecheck_papers_sig", sig)
                except Exception:
                    coverage = None

                llm_budget = LLMBudget(
                    max_total_tokens=int(max_llm_tokens or 0),
                    cost_per_1m_tokens=float(cost_per_1m_tokens),
                    max_cost=float(max_cost),
                )

                llm_client2 = None
                if use_llm or use_llm_review:
                    try:
                        api_cfg2 = llm_api.resolve_config(
                            overrides={
                                "api_key": payload.get("api_key", ""),
                                "base_url": payload.get("base_url", ""),
                                "model": payload.get("model", ""),
                            }
                        )
                        if api_cfg2 and api_cfg2.base_url_v1 and api_cfg2.model and api_cfg2.api_key:
                            llm_client2 = OpenAICompatClient(api_cfg2)
                        else:
                            llm_budget.warnings.append("llm_not_configured: skip llm calls")
                    except Exception:
                        llm_client2 = None
                        llm_budget.warnings.append("llm_not_configured: skip llm calls")

                # Step2 LLM "multi-research-assistant" reviews (white-box, evidence-backed).
                try:
                    if isinstance(result, dict) and use_llm_review:
                        paper_struct = None
                        try:
                            paper_struct = build_material_doc(
                                pdf_path=paper_path,
                                pdf_root=os.path.dirname(os.path.abspath(paper_path)),
                                llm=None,
                            )
                        except Exception:
                            paper_struct = {"headings": [], "citations": []}

                        outlines = []
                        try:
                            mf = materials.load_manifest(exemplar_library)
                            if isinstance(mf, dict):
                                outlines = mf.get("outlines", []) or []
                        except Exception:
                            outlines = []

                        cite_search_fn = None
                        if cite.index_ready(exemplar_library):
                            def _cite_search(q: str, k: int):
                                hits = cite.search(library_name=exemplar_library, query=q, top_k=k)
                                out = []
                                for h in hits or []:
                                    try:
                                        out.append(
                                            (
                                                float(getattr(h, "score", 0.0) or 0.0),
                                                {
                                                    "pdf": str(getattr(h, "pdf", "") or ""),
                                                    "page": int(getattr(h, "page", 0) or 0),
                                                    "sentence": str(getattr(h, "sentence", "") or ""),
                                                    "citations": list(getattr(h, "citations", []) or []),
                                                },
                                            )
                                        )
                                    except Exception:
                                        continue
                                return out

                            cite_search_fn = _cite_search

                        llm_pack = run_llm_audit_pack(
                            audit_result=result,
                            paper_structure=paper_struct if isinstance(paper_struct, dict) else {},
                            exemplar_outlines=outlines if isinstance(outlines, list) else [],
                            rag_search=search_exemplars,
                            cite_search=cite_search_fn,
                            llm=llm_client2,
                            budget=llm_budget,
                            coverage=coverage,
                            max_total_tokens=int(max_llm_tokens or 0),
                            cost_per_1m_tokens=float(cost_per_1m_tokens),
                            max_cost=float(max_cost),
                            progress_cb=prog,
                        )
                        result["llm_reviews"] = llm_pack.get("reviews", {})
                except Exception as e:
                    try:
                        if isinstance(result, dict):
                            result["llm_reviews"] = {"skipped": True, "reason": str(e)[:300]}
                    except Exception:
                        pass

                # Optional: run CiteCheck in the same task (end-to-end audit).
                if include_citecheck and references_library and pdf_root:
                    cfg = CiteCheckConfig(
                        title_match_threshold=float(payload.get("title_match_threshold", 0.55) or 0.55),
                        paragraph_top_k=int(payload.get("paragraph_top_k", 20) or 20),
                        max_pairs=int(payload.get("max_pairs", 800) or 800),
                        use_llm=bool(use_llm and llm_client2 is not None),
                        llm_timeout_s=float(payload.get("timeout_s", 90.0) or 90.0),
                    )

                    def embed_texts(texts):
                        n = 0
                        try:
                            n = int(len(texts or []))
                        except Exception:
                            n = 0
                        progress_cb = None
                        if n >= 20:
                            def _progress(d, t):
                                tasks.set_progress(ts.id, stage="citecheck_embed", done=int(d or 0), total=int(t or 0), detail="构建向量…")

                            progress_cb = _progress
                        return embedder.embed(
                            texts,
                            batch_size=SEMANTIC_EMBED_BATCH,
                            progress_callback=progress_cb,
                            progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                            cancel_event=cancel_event,
                        )

                    runner = CiteCheckRunner(
                        data_dir=get_settings_dir(),
                        embed_texts=embed_texts,
                        model_fingerprint=embedder.model_fingerprint(),
                    )

                    def prog2(stage: str, done: int, total: int, detail: str):
                        st = str(stage or "").strip().lower()
                        tasks.set_progress(ts.id, stage=f"citecheck_{st}", done=done, total=total, detail=detail)

                    citecheck_res = runner.run(
                        main_pdf_path=paper_path,
                        papers_root=pdf_root,
                        library_pdf_root=pdf_root,
                        cfg=cfg,
                        llm=llm_client2,
                        budget=llm_budget,
                        coverage=coverage,
                        cancel_cb=cancel_event.is_set,
                        progress_cb=prog2,
                    )
                    if cancel_event.is_set():
                        tasks.finish(ts.id, status="canceled", error="")
                        return
                    if isinstance(result, dict):
                        result["citecheck"] = citecheck_res

                # Attach final LLM usage stats (combined: llm_review + citecheck).
                try:
                    if isinstance(result, dict):
                        result["llm_usage"] = {
                            "calls": int(llm_budget.calls),
                            "prompt_tokens": int(llm_budget.prompt_tokens),
                            "completion_tokens": int(llm_budget.completion_tokens),
                            "total_tokens": int(llm_budget.total_tokens),
                            "approx_total_tokens": int(llm_budget.approx_total_tokens),
                            "max_total_tokens": int(getattr(llm_budget, "max_total_tokens", 0) or 0),
                            "remaining_tokens": int(getattr(llm_budget, "budget_remaining_tokens", lambda: 0)() or 0),
                            "cost_per_1m_tokens": float(getattr(llm_budget, "cost_per_1m_tokens", 0.0) or 0.0),
                            "estimated_cost": float(getattr(llm_budget, "estimated_cost", lambda: 0.0)() or 0.0),
                            "max_cost": float(getattr(llm_budget, "max_cost", 0.0) or 0.0),
                            "warnings": list(llm_budget.warnings),
                        }
                except Exception:
                    pass

                # Persist coverage (best-effort).
                if coverage is not None:
                    try:
                        coverage.save()
                    except Exception:
                        pass

                try:
                    tmp = out_path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, out_path)
                except Exception:
                    pass
                tasks.finish(ts.id, status="done", error="")
            except Exception:
                if cancel_event.is_set():
                    tasks.finish(ts.id, status="canceled", error="")
                else:
                    tasks.finish(ts.id, status="failed", error=traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        return {"task_id": ts.id}

    @app.get("/api/audit/result")
    def audit_result(task_id: str):
        task_id = (task_id or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        path = os.path.join(_audit_jobs_dir(), f"{task_id}.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="result not found")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {}

    @app.get("/api/audit/report.md")
    def audit_report_markdown(task_id: str):
        task_id = (task_id or "").strip()
        if not task_id:
            raise HTTPException(status_code=400, detail="task_id required")
        path = os.path.join(_audit_jobs_dir(), f"{task_id}.json")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="result not found")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        md = audit_to_markdown(data)
        return Response(content=md, media_type="text/markdown; charset=utf-8")

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
            if len(txt) > 650:
                txt = txt[:650].rstrip() + "…"
            exemplars.append({"id": cid, "score": float(sc or 0.0), "pdf": pdf, "page": page, "text": txt})
            excerpt = f"[{pdf}#p{page}] {txt}"
            c_list.append((cid, excerpt))
            allowed_quotes[cid] = excerpt

        out: Dict[str, Any] = {"selected_text": selected, "exemplars": exemplars}
        if not do_generate:
            return out

        try:
            api_cfg = llm_api.resolve_config(
                overrides={
                    "api_key": payload.get("api_key", ""),
                    "base_url": payload.get("base_url", ""),
                    "model": payload.get("model", ""),
                }
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not api_cfg.api_key:
            raise HTTPException(status_code=400, detail="missing api key (set SKILL_LLM_API_KEY / OPENAI_API_KEY)")
        if not api_cfg.base_url_v1:
            raise HTTPException(status_code=400, detail="missing base_url (set SKILL_LLM_BASE_URL / OPENAI_BASE_URL)")
        if not api_cfg.model:
            raise HTTPException(status_code=400, detail="missing model (set SKILL_LLM_MODEL / OPENAI_MODEL)")
        api_client = OpenAICompatClient(api_cfg)

        lang = LanguageDetector.detect(selected)
        prompt = build_polish_prompt(selected_text=selected, citations=c_list, language=lang, compact=True)
        messages = [
            {"role": "system", "content": "Return STRICT JSON only."},
            {"role": "user", "content": prompt},
        ]

        temperature = float(payload.get("temperature", 0.0) or 0.0)
        max_tokens_requested = int(payload.get("max_tokens", 4096) or 4096)
        max_cap = 8192
        max_tokens = max(64, min(int(max_tokens_requested), int(max_cap)))
        retries = int(payload.get("retries", 2) or 2)
        retries = max(0, min(retries, 3))

        last_err = ""
        last_output = ""
        parsed = None
        for attempt in range(1, retries + 2):
            timeout_s = float(payload.get("timeout_s", 180.0) or 180.0)
            status, resp = api_client.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                timeout_s=timeout_s,
            )
            if int(status or 0) != 200 or not isinstance(resp, dict):
                last_err = f"http {status}"
                err_preview = ""
                try:
                    if isinstance(resp, dict):
                        if isinstance(resp.get("error", None), dict):
                            err_preview = (resp.get("error", {}) or {}).get("message", "") or ""
                        if not err_preview:
                            err_preview = str(resp.get("_raw", "") or resp.get("_error", "") or "").strip()
                except Exception:
                    err_preview = ""
                if err_preview:
                    if len(err_preview) > 500:
                        err_preview = err_preview[:500] + "…"
                    last_err = f"{last_err}: {err_preview}"
                code = 400 if int(status or 0) in (401, 403) else 502
                raise HTTPException(status_code=code, detail=f"api request failed: {last_err}")
            content = extract_first_content(resp)
            last_output = content or ""
            data = extract_json(content) if content else None
            if not isinstance(data, dict):
                last_err = "bad json"
                # Output may be truncated or malformed. Auto-increase output budget and ask the model
                # to re-emit a complete JSON object (do NOT repeat the whole prompt to keep it short).
                if max_tokens < max_cap:
                    max_tokens = min(max_cap, max(4096, max_tokens * 2))
                if content:
                    preview = (content or "").strip()
                    if len(preview) > 2000:
                        preview = preview[:2000].rstrip() + "…"
                    messages = [
                        {"role": "system", "content": "Return STRICT JSON only."},
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": preview},
                        {
                            "role": "user",
                            "content": "\n".join(
                                [
                                    "Your previous output was incomplete or invalid JSON.",
                                    "Output ONE complete JSON object matching OUTPUT_SCHEMA.",
                                    "Do not add any extra keys. Do not include markdown.",
                                    "Keep all strings short to avoid truncation.",
                                ]
                            ).strip(),
                        },
                    ]
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
                detail += " (output may be truncated; try increasing max_tokens, e.g. 8192 for Gemini)"
            raise HTTPException(status_code=500, detail=detail)

        used_llm = {"provider": "api", "base_url": api_cfg.base_url_v1, "model": api_cfg.model}

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
        out["llm"] = used_llm

        # Optional: compute alignment score before/after (white-box, no LLM).
        try:
            def _score_pack(query: str) -> dict:
                rs = rag.search(library_name=library_name, query=query, top_k=3)
                ex2 = []
                best = None
                for sc, node in (rs or [])[:3]:
                    try:
                        pdf = getattr(node, "pdf", "") or ""
                        page = int(getattr(node, "page", 0) or 0)
                        txt = (getattr(node, "text", "") or "").strip()
                    except Exception:
                        pdf, page, txt = "", 0, ""
                    ex2.append({"score": float(sc or 0.0), "pdf": pdf, "page": page, "text": txt})
                    if best is None:
                        best = ex2[-1]
                best_score = float((best or {}).get("score", 0.0) or 0.0)
                pct = int(max(0.0, min(1.0, best_score)) * 100)
                return {"score": best_score, "pct": pct, "best": best or {}, "exemplars": ex2}

            alignment = {"selected": _score_pack(selected), "variants": []}
            for v in out["result"].get("variants", []) or []:
                lvl = str(v.get("level", "") or "").strip().lower()
                rw = str(v.get("rewrite", "") or "").strip()
                if not rw:
                    continue
                pack = _score_pack(rw)
                pack["level"] = lvl or "unknown"
                alignment["variants"].append(pack)
            out["alignment"] = alignment
        except Exception:
            pass
        return out

    @app.post("/api/library/open_pdf")
    def open_library_pdf(payload: dict = Body(...)):
        library_name = (payload.get("library", "") or "").strip()
        pdf_rel = (payload.get("pdf", "") or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_rel:
            raise HTTPException(status_code=400, detail="pdf required")

        pdf_root = _resolve_library_pdf_root(library_name)
        if not pdf_root:
            raise HTTPException(status_code=400, detail="pdf root missing (import PDFs first)")

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

    @app.get("/api/library/pdf")
    def get_library_pdf(library: str, pdf: str):
        library_name = (library or "").strip()
        pdf_rel = (pdf or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_rel:
            raise HTTPException(status_code=400, detail="pdf required")

        pdf_root = _resolve_library_pdf_root(library_name)
        if not pdf_root:
            raise HTTPException(status_code=400, detail="pdf root missing (import PDFs first)")

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

        return FileResponse(
            full,
            media_type="application/pdf",
            filename=os.path.basename(full),
            content_disposition_type="inline",
        )

    @app.get("/api/library/pdf_page.png")
    def get_library_pdf_page_png(library: str, pdf: str, page: int = 1, scale: float = 1.6):
        library_name = (library or "").strip()
        pdf_rel = (pdf or "").strip()
        if not library_name:
            raise HTTPException(status_code=400, detail="library required")
        if not pdf_rel:
            raise HTTPException(status_code=400, detail="pdf required")

        page_i = int(page or 1)
        if page_i <= 0:
            page_i = 1
        scale_f = float(scale or 1.6)
        if not (0.75 <= scale_f <= 2.5):
            scale_f = 1.6

        pdf_root = _resolve_library_pdf_root(library_name)
        if not pdf_root:
            raise HTTPException(status_code=400, detail="pdf root missing (import PDFs first)")

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

        slug = _library_slug(library_manager, library_name)
        cache_path = _preview_cache_path("library", slug or library_name, full, page_i, scale_f)
        png_path = _ensure_pdf_page_png(full, page_i, scale_f, cache_path)
        return FileResponse(
            png_path,
            media_type="image/png",
            filename=os.path.basename(png_path),
            content_disposition_type="inline",
        )

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
