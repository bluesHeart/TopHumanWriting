# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .library import LibraryBuildConfig, LibraryBuilder
from .models import default_semantic_dir, download_semantic_model, semantic_model_status
from .runner import AuditRunConfig, AuditRunner
from .workspace import Workspace


def _slugify(name: str) -> str:
    s = str(name or "").strip()
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "default"


@dataclass(frozen=True)
class AuditExport:
    export_dir: str
    result: Dict[str, Any]

    @property
    def result_json_path(self) -> str:
        return str(Path(self.export_dir) / "result.json")

    @property
    def report_md_path(self) -> str:
        return str(Path(self.export_dir) / "report.md")


@dataclass(frozen=True)
class Profile:
    top_k: int = 20
    paragraph_top_k: int = 20
    max_pairs: int = 800
    max_sentences: int = 3600
    low_alignment_threshold: float = 0.35


PROFILES: Dict[str, Profile] = {
    # Retrieval-heavy but still within small-cost budget by default.
    "standard": Profile(),
    # Faster/cheaper.
    "cheap": Profile(top_k=12, paragraph_top_k=12, max_pairs=300, max_sentences=1600, low_alignment_threshold=0.40),
    # Broader coverage (may spend more tokens; still capped by max_llm_tokens).
    "deep": Profile(top_k=30, paragraph_top_k=30, max_pairs=1400, max_sentences=5200, low_alignment_threshold=0.32),
}


class TopHumanWriting:
    """
    sklearn-style facade:

      - fit(): build reusable exemplar library artifacts (slow, one-time)
      - audit(): run end-to-end audit against artifacts (repeatable)
      - run(): fit_if_needed + audit (single-call UX)
    """

    def __init__(
        self,
        exemplars: str,
        *,
        library_name: str = "",
        data_dir: str = "",
        rag_backend: str = "auto",
        semantic_model_dir: str = "",
        auto_download_semantic: bool = True,
    ):
        self.exemplars = str(exemplars or "").strip()
        if not self.exemplars:
            raise ValueError("exemplars folder required")

        self.library_name = (library_name or "").strip() or _slugify(Path(self.exemplars).name)
        self.rag_backend = (rag_backend or "").strip() or "auto"
        self.semantic_model_dir = (semantic_model_dir or "").strip()
        self.auto_download_semantic = bool(auto_download_semantic)

        self.ws = Workspace(Path(data_dir)) if str(data_dir or "").strip() else Workspace.from_env()
        self.ws.ensure_dirs()

        self._builder = LibraryBuilder(self.ws)
        self._runner = AuditRunner(self.ws)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TopHumanWriting(exemplars={self.exemplars!r}, library_name={self.library_name!r}, "
            f"data_dir={str(self.ws.data_dir)!r}, rag_backend={self.rag_backend!r})"
        )

    def status(self) -> Dict[str, Any]:
        st = self._builder.status(name=self.library_name)
        try:
            from dataclasses import asdict

            return asdict(st)
        except Exception:
            return {"name": getattr(st, "name", self.library_name)}

    def ensure_semantic_model(self, *, download_if_missing: Optional[bool] = None) -> str:
        """
        Ensure the local semantic embedding model exists.

        Returns: resolved semantic model directory.
        """

        # User-specified dir has highest priority.
        if self.semantic_model_dir:
            p = Path(self.semantic_model_dir).resolve()
            if semantic_model_status(p).ok:
                return str(p)

        # Env override.
        env_dir = (os.environ.get("TOPHUMANWRITING_SEMANTIC_MODEL_DIR", "") or "").strip()
        if env_dir:
            p2 = Path(env_dir).resolve()
            if semantic_model_status(p2).ok:
                self.semantic_model_dir = str(p2)
                return self.semantic_model_dir

        # Default writable location (data_dir/models/semantic).
        dest = default_semantic_dir(workspace=self.ws)
        st = semantic_model_status(dest)
        if st.ok:
            self.semantic_model_dir = str(dest)
            return self.semantic_model_dir

        allow = self.auto_download_semantic if download_if_missing is None else bool(download_if_missing)
        if not allow:
            raise RuntimeError(
                "Missing semantic embedding model. Run: thw models download-semantic "
                "or set TOPHUMANWRITING_SEMANTIC_MODEL_DIR."
            )

        download_semantic_model(dest_dir=dest, force=False, timeout_s=180.0, max_retries=3, progress_cb=None)
        self.semantic_model_dir = str(dest)
        return self.semantic_model_dir

    def fit(
        self,
        *,
        force: bool = False,
        with_rag: bool = True,
        with_cite: bool = True,
        with_materials: bool = True,
        with_vocab: bool = True,
        materials_use_llm: bool = False,
        ensure_models: bool = True,
        progress: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> "TopHumanWriting":
        """
        Build (or reuse) exemplar library artifacts.
        """

        semantic_dir = None
        if ensure_models:
            semantic_dir = self.ensure_semantic_model()
        elif self.semantic_model_dir:
            semantic_dir = self.semantic_model_dir

        # Ensure RagIndexer picks the intended backend (esp. when manifest is old).
        if self.rag_backend and self.rag_backend not in ("auto", ""):
            os.environ["TOPHUMANWRITING_RAG_BACKEND"] = self.rag_backend

        cfg = LibraryBuildConfig(
            name=self.library_name,
            pdf_root=self.exemplars,
            semantic_model_dir=str(semantic_dir or "") or None,
            build_rag=bool(with_rag),
            build_cite=bool(with_cite),
            build_materials=bool(with_materials),
            build_vocab=bool(with_vocab),
            force_rebuild=bool(force),
            materials_use_llm=bool(materials_use_llm),
        )
        self._builder.build(cfg, progress_cb=progress)
        return self

    def audit(
        self,
        paper_pdf: str,
        *,
        profile: str = "standard",
        auto_fit: bool = True,
        ensure_models: bool = True,
        use_llm: bool = True,
        max_llm_tokens: int = 200_000,
        max_cost: float = 0.0,
        cost_per_1m_tokens: float = 0.0,
        max_pages: int = 0,
        export_name: str = "",
        progress: Optional[Callable[[str, int, int, str], None]] = None,
        **overrides: Any,
    ) -> AuditExport:
        """
        Run end-to-end audit and write an export bundle.
        """

        paper_path = str(paper_pdf or "").strip()
        if not paper_path:
            raise ValueError("paper_pdf required")

        prof = PROFILES.get(str(profile or "").strip().lower(), PROFILES["standard"])

        if auto_fit:
            st = self._builder.status(name=self.library_name)
            if not (st.rag_ready and st.cite_ready and st.materials_ready and st.vocab_ready):
                self.fit(ensure_models=ensure_models, progress=progress)

        if ensure_models:
            _ = self.ensure_semantic_model(download_if_missing=True)

        if self.rag_backend and self.rag_backend not in ("auto", ""):
            os.environ["TOPHUMANWRITING_RAG_BACKEND"] = self.rag_backend

        # Backward-compatible overrides (deprecated): *_rmb naming was historical.
        if "max_cost_rmb" in overrides and "max_cost" not in overrides:
            max_cost = float(overrides.pop("max_cost_rmb") or 0.0)
        if "cost_per_1m_tokens_rmb" in overrides and "cost_per_1m_tokens" not in overrides:
            cost_per_1m_tokens = float(overrides.pop("cost_per_1m_tokens_rmb") or 0.0)

        cfg = AuditRunConfig(
            paper_pdf_path=str(paper_path),
            exemplar_library=self.library_name,
            top_k=int(overrides.pop("top_k", prof.top_k)),
            paragraph_top_k=int(overrides.pop("paragraph_top_k", prof.paragraph_top_k)),
            max_pairs=int(overrides.pop("max_pairs", prof.max_pairs)),
            max_sentences=int(overrides.pop("max_sentences", prof.max_sentences)),
            low_alignment_threshold=float(overrides.pop("low_alignment_threshold", prof.low_alignment_threshold)),
            max_pages=int(max_pages),
            use_llm=bool(use_llm),
            max_llm_tokens=int(max_llm_tokens or 0),
            cost_per_1m_tokens=float(cost_per_1m_tokens),
            max_cost=float(max_cost),
            export_name=str(export_name or ""),
            **overrides,
        )

        export_dir, result = self._runner.run(cfg, progress_cb=progress)
        return AuditExport(export_dir=export_dir, result=result)

    def run(
        self,
        paper_pdf: str,
        *,
        profile: str = "standard",
        max_llm_tokens: int = 200_000,
        max_cost: float = 0.0,
        cost_per_1m_tokens: float = 0.0,
        progress: Optional[Callable[[str, int, int, str], None]] = None,
        **kwargs: Any,
    ) -> AuditExport:
        """
        One-call UX: fit_if_needed + audit.
        """

        return self.audit(
            paper_pdf,
            profile=profile,
            auto_fit=True,
            max_llm_tokens=int(max_llm_tokens or 0),
            max_cost=float(max_cost),
            cost_per_1m_tokens=float(cost_per_1m_tokens),
            progress=progress,
            **kwargs,
        )
