# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .workspace import Workspace


class LibraryBuildError(RuntimeError):
    pass


def _resolve_semantic_model_dir(*, explicit: Optional[str] = None) -> str:
    """
    Resolve local ONNX embedder directory.

    Priority:
      1) explicit argument
      2) env TOPHUMANWRITING_SEMANTIC_MODEL_DIR
      3) <data_dir>/models/semantic (recommended; writable for pip installs)
      4) ./models/semantic (repo layout)
      5) <get_app_dir()>/models/semantic (when running from repo)
    """

    if explicit:
        p = os.path.abspath(str(explicit))
        if os.path.exists(p):
            return p

    env_dir = (os.environ.get("TOPHUMANWRITING_SEMANTIC_MODEL_DIR", "") or "").strip()
    if env_dir:
        p = os.path.abspath(env_dir)
        if os.path.exists(p):
            return p

    # Preferred: a writable workspace location (pip installs can't write into site-packages).
    try:
        ws_candidate = os.path.abspath(os.path.join(str(Workspace.from_env().data_dir), "models", "semantic"))
        if os.path.exists(ws_candidate):
            return ws_candidate
    except Exception:
        pass

    repo_candidate = os.path.abspath(os.path.join(os.getcwd(), "models", "semantic"))
    if os.path.exists(repo_candidate):
        return repo_candidate

    try:
        from ai_word_detector import get_app_dir  # type: ignore

        app_candidate = os.path.abspath(os.path.join(get_app_dir(), "models", "semantic"))
        if os.path.exists(app_candidate):
            return app_candidate
    except Exception:
        pass

    raise LibraryBuildError(
        "Missing semantic model dir. Set TOPHUMANWRITING_SEMANTIC_MODEL_DIR, "
        "or run: thw models download-semantic, "
        "or place the ONNX model under ./models/semantic."
    )


def _load_embedder(*, semantic_model_dir: str):
    try:
        from ai_word_detector import SemanticEmbedder  # type: ignore
    except Exception as e:  # pragma: no cover
        raise LibraryBuildError(f"Cannot import SemanticEmbedder: {e}") from e
    return SemanticEmbedder(semantic_model_dir, model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2")


def _default_progress(_stage: str, _done: int, _total: int, _detail: str) -> None:
    return None


@dataclass(frozen=True)
class LibraryBuildConfig:
    name: str
    pdf_root: str
    semantic_model_dir: Optional[str] = None
    build_rag: bool = True
    build_cite: bool = True
    build_materials: bool = True
    build_vocab: bool = True
    force_rebuild: bool = False
    materials_use_llm: bool = False


@dataclass(frozen=True)
class LibraryStatus:
    name: str
    pdf_root: str
    rag_ready: bool
    cite_ready: bool
    materials_ready: bool
    vocab_ready: bool


class LibraryBuilder:
    """
    Build reusable "exemplar library" artifacts.

    Think of this as `fit()`:
      - slow, one-time (or incremental)
      - produces a persistent artifact folder
    """

    def __init__(self, workspace: Optional[Workspace] = None):
        self.ws = workspace or Workspace.from_env()
        self.ws.ensure_dirs()

    def status(self, *, name: str) -> LibraryStatus:
        lib = (name or "").strip()
        if not lib:
            raise ValueError("library name required")

        # pdf_root best-effort from manifests
        pdf_root = ""
        rag_ready = False
        cite_ready = False
        materials_ready = False
        vocab_ready = False

        try:
            from aiwd.rag_index import RagIndexer  # type: ignore

            rag = RagIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            m = {}
            try:
                m = json_load(rag.manifest_path)
            except Exception:
                m = {}
            pdf_root = str(m.get("pdf_root", "") or "") if isinstance(m, dict) else ""
            rag_ready = os.path.exists(os.path.join(rag.storage_dir, "docstore.json"))
        except Exception:
            rag_ready = False

        try:
            from aiwd.citation_bank import CitationBankIndexer  # type: ignore

            cite = CitationBankIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            m = cite.load_manifest()
            if not pdf_root:
                pdf_root = str(m.get("pdf_root", "") or "")
            cite_ready = cite.index_ready()
        except Exception:
            cite_ready = False

        try:
            from aiwd.materials import MaterialsIndexer  # type: ignore

            mat = MaterialsIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            m = mat.load_manifest()
            if not pdf_root:
                pdf_root = str(m.get("pdf_root", "") or "")
            materials_ready = mat.index_ready()
        except Exception:
            materials_ready = False

        try:
            vocab_path = self.ws.vocab_library_path(lib)
            vocab_ready = vocab_path.exists() and vocab_path.stat().st_size > 100
        except Exception:
            vocab_ready = False

        return LibraryStatus(
            name=lib,
            pdf_root=pdf_root,
            rag_ready=bool(rag_ready),
            cite_ready=bool(cite_ready),
            materials_ready=bool(materials_ready),
            vocab_ready=bool(vocab_ready),
        )

    def build(
        self,
        cfg: LibraryBuildConfig,
        *,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> LibraryStatus:
        lib = (cfg.name or "").strip()
        if not lib:
            raise ValueError("cfg.name required")

        pdf_root = os.path.abspath((cfg.pdf_root or "").strip())
        if not pdf_root or not os.path.exists(pdf_root):
            raise FileNotFoundError(pdf_root)

        progress_cb = progress_cb or _default_progress

        # Shared semantic embedder (local ONNX)
        semantic_dir = _resolve_semantic_model_dir(explicit=cfg.semantic_model_dir)
        embedder = _load_embedder(semantic_model_dir=semantic_dir)

        def embed_texts(texts, progress_cb2=None, cancel_cb2=None):
            def _report(done: int, total: int):
                if progress_cb2:
                    try:
                        progress_cb2(int(done), int(total))
                    except Exception:
                        pass

            return embedder.embed(
                list(texts or []),
                batch_size=8,
                progress_callback=_report if progress_cb2 else None,
                progress_every_s=0.5,
                cancel_event=None,
            )

        def embed_query(q: str):
            vecs = embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
            try:
                return vecs[0]
            except Exception:
                return vecs

        if cfg.build_rag:
            from aiwd.rag_index import RagIndexer  # type: ignore

            rag = RagIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            if cfg.force_rebuild or not os.path.exists(os.path.join(rag.storage_dir, "docstore.json")):
                progress_cb("rag", 0, 0, "building")
                rag.build(
                    pdf_root,
                    embed_sentences=embed_texts,
                    embed_query=embed_query,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                )

        if cfg.build_cite:
            from aiwd.citation_bank import CitationBankIndexer  # type: ignore

            cite = CitationBankIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            if cfg.force_rebuild or not cite.index_ready():
                progress_cb("cite", 0, 0, "building")
                cite.build(
                    pdf_root=pdf_root,
                    embed_sentences=embed_texts,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                    max_pages=None,
                )

        if cfg.build_materials:
            from aiwd.materials import MaterialsIndexer  # type: ignore

            mat = MaterialsIndexer(data_dir=str(self.ws.data_dir), library_name=lib)
            if cfg.force_rebuild or not mat.index_ready():
                progress_cb("materials", 0, 0, "building")
                mat.build(
                    pdf_root=pdf_root,
                    use_llm=bool(cfg.materials_use_llm),
                    llm=None,
                    progress_cb=progress_cb,
                    cancel_cb=cancel_cb,
                )

        if cfg.build_vocab:
            # Word doc-frequency baseline for lexical stats (no LLM).
            try:
                from ai_word_detector import AcademicCorpus, LibraryManager  # type: ignore
            except Exception as e:
                raise LibraryBuildError(f"Cannot import AcademicCorpus/LibraryManager: {e}") from e

            lm = LibraryManager()
            lib_path = lm.get_library_path(lib)
            if not cfg.force_rebuild:
                try:
                    if os.path.exists(lib_path) and os.path.getsize(lib_path) > 100:
                        progress_cb("vocab", 1, 1, "ready")
                        return self.status(name=lib)
                except Exception:
                    pass
            os.makedirs(os.path.dirname(lib_path), exist_ok=True)
            corpus = AcademicCorpus(lib_path)

            # This is a pure scan; keep it deterministic and low-risk (no semantic/syntax here).
            def _pdf_prog(done: int, total: int, detail: str = ""):
                progress_cb("vocab_scan", int(done), int(total), str(detail or ""))

            t0 = time.time()
            corpus.process_pdf_folder(
                pdf_root,
                _pdf_prog,
                semantic_embedder=None,
                semantic_progress_callback=None,
                syntax_analyzer=None,
                syntax_progress_callback=None,
                cancel_event=None,
            )
            corpus.save_vocabulary()
            progress_cb("vocab_done", 1, 1, f"seconds={time.time()-t0:.1f}")

        return self.status(name=lib)


def json_load(path: str) -> dict:
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
