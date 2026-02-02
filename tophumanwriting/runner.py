# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .workspace import Workspace


class AuditRunError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    source: str  # "env" | "settings" | "mixed" | "missing"


def _now_slug() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _dump_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def resolve_llm_config() -> LLMConfig:
    """
    Resolve OpenAI-compatible LLM config for end users.

    Priority:
      1) env TOPHUMANWRITING_LLM_* (or SKILL_LLM_*/OPENAI_*)
      2) <data_dir>/settings.json (via ai_word_detector.Settings)
    """

    def _env_any(names: List[str]) -> str:
        for n in names:
            v = (os.environ.get(n, "") or "").strip()
            if v:
                return v
        return ""

    api_key_env = _env_any(["TOPHUMANWRITING_LLM_API_KEY", "SKILL_LLM_API_KEY", "OPENAI_API_KEY"])
    base_url_env = _env_any(["TOPHUMANWRITING_LLM_BASE_URL", "SKILL_LLM_BASE_URL", "OPENAI_BASE_URL"])
    model_env = _env_any(["TOPHUMANWRITING_LLM_MODEL", "SKILL_LLM_MODEL", "OPENAI_MODEL"])

    api_key = api_key_env
    base_url = base_url_env
    model = model_env

    api_key_set = bool(api_key)
    base_set = bool(base_url)
    model_set = bool(model)

    api_key_settings = ""
    base_url_settings = ""
    model_settings = ""
    if not (api_key_set and base_set and model_set):
        try:
            from ai_word_detector import Settings  # type: ignore

            s = Settings()
            api_key_settings = str(s.get("llm_api_key", "") or "").strip()
            base_url_settings = str(s.get("llm_api_base_url", "") or "").strip()
            model_settings = str(s.get("llm_api_model", "") or "").strip()
        except Exception:
            api_key_settings = ""
            base_url_settings = ""
            model_settings = ""

    if not api_key:
        api_key = api_key_settings
    if not base_url:
        base_url = base_url_settings
    if not model:
        model = model_settings

    if not (api_key and base_url and model):
        return LLMConfig(api_key=api_key or "", base_url=base_url or "", model=model or "", source="missing")

    sources = set()
    if api_key_env or base_url_env or model_env:
        sources.add("env")
    if api_key_settings or base_url_settings or model_settings:
        sources.add("settings")
    source = "mixed" if len(sources) >= 2 else (list(sources)[0] if sources else "missing")
    return LLMConfig(api_key=api_key, base_url=base_url, model=model, source=source)


def _load_llm(*, timeout_s: float = 90.0):
    from aiwd.openai_compat import OpenAICompatClient, OpenAICompatConfig  # type: ignore

    cfg0 = resolve_llm_config()
    if cfg0.source == "missing":
        return None
    cfg = OpenAICompatConfig(
        api_key=cfg0.api_key,
        base_url=cfg0.base_url,
        model=cfg0.model,
        timeout_s=float(timeout_s),
        max_retries=5,
        base_retry_delay_s=0.9,
        max_retry_delay_s=10.0,
    )
    return OpenAICompatClient(cfg)


@dataclass(frozen=True)
class AuditRunConfig:
    paper_pdf_path: str
    exemplar_library: str = "reference_papers"
    series_id: str = ""
    top_k: int = 20
    max_pages: int = 0
    max_sentences: int = 3600
    min_sentence_len: int = 20
    low_alignment_threshold: float = 0.35

    include_citecheck: bool = True
    references_pdf_root: str = ""  # default: exemplar pdf_root from manifests, else ./reference_papers
    title_match_threshold: float = 0.55
    paragraph_top_k: int = 20
    max_pairs: int = 800

    use_llm: bool = True
    max_llm_tokens: int = 200_000
    cost_per_1m_tokens: float = 0.0  # optional estimate only (unitless)
    max_cost: float = 0.0  # optional max cost estimate (unitless)
    llm_timeout_s: float = 90.0

    export_name: str = ""  # folder name under <data_dir>/audit/exports


class AuditRunner:
    """
    Run an end-to-end paper audit against a reusable exemplar library.

    Think of this as `transform()`:
      - uses existing artifacts (RAG/citation/materials/vocab)
      - produces a deterministic export bundle (result.json + report.md)
    """

    def __init__(self, workspace: Optional[Workspace] = None):
        self.ws = workspace or Workspace.from_env()
        self.ws.ensure_dirs()

    def run(
        self,
        cfg: AuditRunConfig,
        *,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        paper_path = os.path.abspath((cfg.paper_pdf_path or "").strip())
        if not paper_path or not os.path.exists(paper_path):
            raise FileNotFoundError(paper_path)

        library = (cfg.exemplar_library or "").strip()
        if not library:
            raise ValueError("cfg.exemplar_library required")

        series_id = (cfg.series_id or "").strip() or Path(paper_path).stem

        # Local embedder (for RAG query + CiteCheck retrieval)
        from tophumanwriting.library import _load_embedder, _resolve_semantic_model_dir  # noqa: WPS433

        semantic_dir = _resolve_semantic_model_dir(explicit=None)
        embedder = _load_embedder(semantic_model_dir=semantic_dir)

        def embed_query(q: str):
            vecs = embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
            try:
                return vecs[0]
            except Exception:
                return vecs

        # RAG search session (must exist)
        from aiwd.rag_index import RagIndexer  # type: ignore

        rag_ix = RagIndexer(data_dir=str(self.ws.data_dir), library_name=library)
        rag_sess = rag_ix.create_session(embed_query=embed_query)

        def rag_search(query: str, k: int) -> List[Tuple[float, Dict[str, Any]]]:
            hits = rag_sess.search(query, top_k=int(k or 8))
            out: List[Tuple[float, Dict[str, Any]]] = []
            for sc, node in hits:
                out.append(
                    (
                        float(sc or 0.0),
                        {"pdf": getattr(node, "pdf", "") or "", "page": int(getattr(node, "page", 0) or 0), "text": getattr(node, "text", "") or ""},
                    )
                )
            return out

        # Load exemplar vocabulary (doc_freq baseline) if available.
        corpus = None
        try:
            from ai_word_detector import AcademicCorpus, LibraryManager  # type: ignore

            lib_path = LibraryManager().get_library_path(library)
            c0 = AcademicCorpus(lib_path)
            if c0.load_vocabulary():
                corpus = c0
        except Exception:
            corpus = None

        # Coverage store (avoid repeated checks across runs).
        from aiwd.review_coverage import ReviewCoverageStore  # type: ignore

        coverage = ReviewCoverageStore.load_or_create(dir_path=str(self.ws.audit_coverage_dir()), series_id=series_id)

        # 1) Core non-LLM audit (alignment + heuristics)
        from aiwd.audit import run_full_paper_audit  # type: ignore

        t0 = time.time()
        result = run_full_paper_audit(
            paper_pdf_path=paper_path,
            exemplar_library=library,
            search_exemplars=lambda q, k: rag_search(q, int(k)),
            corpus=corpus,
            syntax_analyzer=None,
            max_pages=(int(cfg.max_pages) if int(cfg.max_pages or 0) > 0 else None),
            max_sentences=int(cfg.max_sentences),
            min_sentence_len=int(cfg.min_sentence_len),
            top_k=int(cfg.top_k),
            low_alignment_threshold=float(cfg.low_alignment_threshold),
            include_style=True,
            include_repetition=True,
            include_syntax=True,
            cancel_cb=None,
            progress_cb=progress_cb,
        )

        # Attach identifiers for downstream consumption.
        try:
            if isinstance(result, dict):
                meta2 = result.get("meta", {})
                if isinstance(meta2, dict):
                    meta2["series_id"] = series_id
                    meta2["elapsed_s"] = float(time.time() - t0)
        except Exception:
            pass

        # Paper structure (paragraphs/headings/citation sentences).
        from aiwd.materials import build_material_doc, MaterialsIndexer  # type: ignore

        paper_struct = build_material_doc(pdf_path=paper_path, pdf_root=str(Path(paper_path).parent), llm=None)

        outlines: List[Dict[str, Any]] = []
        pdf_root_from_manifest = ""
        try:
            m = MaterialsIndexer(data_dir=str(self.ws.data_dir), library_name=library).load_manifest()
            pdf_root_from_manifest = str(m.get("pdf_root", "") or "").replace("/", os.sep)
            outlines_raw = m.get("outlines", []) if isinstance(m, dict) else []
            if isinstance(outlines_raw, list):
                outlines = [x for x in outlines_raw if isinstance(x, dict)]
        except Exception:
            outlines = []

        # Citation-style exemplar search (optional).
        cite_search_fn = None
        try:
            from aiwd.citation_bank import CitationBankIndexer  # type: ignore

            cite_ix = CitationBankIndexer(data_dir=str(self.ws.data_dir), library_name=library)
            if cite_ix.index_ready():
                cite_sess = cite_ix.create_session(embed_query=embed_query)

                def _cite_search(query: str, k: int) -> List[Tuple[float, Dict[str, Any]]]:
                    hits = cite_sess.search(query, top_k=int(k or 8))
                    out2: List[Tuple[float, Dict[str, Any]]] = []
                    for h in hits:
                        out2.append(
                            (
                                float(getattr(h, "score", 0.0) or 0.0),
                                {
                                    "pdf": getattr(h, "pdf", "") or "",
                                    "page": int(getattr(h, "page", 0) or 0),
                                    "sentence": getattr(h, "sentence", "") or "",
                                    "citations": list(getattr(h, "citations", []) or []),
                                },
                            )
                        )
                    return out2

                cite_search_fn = _cite_search
        except Exception:
            cite_search_fn = None

        # LLM client + shared budget (LLM review + CiteCheck).
        from aiwd.llm_budget import LLMBudget  # type: ignore

        llm = _load_llm(timeout_s=float(cfg.llm_timeout_s)) if bool(cfg.use_llm) else None
        budget = LLMBudget(
            max_total_tokens=int(cfg.max_llm_tokens or 0),
            cost_per_1m_tokens=float(cfg.cost_per_1m_tokens),
            max_cost=float(cfg.max_cost),
        )

        # 2) LLM reviews
        if llm is not None:
            from aiwd.llm_review import run_llm_audit_pack  # type: ignore

            try:
                pack = run_llm_audit_pack(
                    audit_result=result,
                    paper_structure=paper_struct if isinstance(paper_struct, dict) else {},
                    exemplar_outlines=outlines,
                    rag_search=rag_search,
                    cite_search=cite_search_fn,
                    llm=llm,
                    budget=budget,
                    coverage=coverage,
                    cost_per_1m_tokens=float(cfg.cost_per_1m_tokens),
                    max_cost=float(cfg.max_cost),
                    progress_cb=progress_cb,
                )
                if isinstance(result, dict):
                    result["llm_reviews"] = pack.get("reviews", {}) if isinstance(pack, dict) else {}
            except Exception as e:
                if isinstance(result, dict):
                    result["llm_reviews"] = {"skipped": True, "reason": str(e)[:300]}

        # 3) CiteCheck (optional; can work without LLM)
        if bool(cfg.include_citecheck):
            from aiwd.cite_check import CiteCheckConfig, CiteCheckRunner  # type: ignore

            pdf_root = (cfg.references_pdf_root or "").strip()
            if not pdf_root:
                pdf_root = (pdf_root_from_manifest or "").strip()
            if not pdf_root:
                pdf_root = os.path.abspath(os.path.join(os.getcwd(), "reference_papers"))

            if os.path.exists(pdf_root):
                cfg2 = CiteCheckConfig(
                    title_match_threshold=float(cfg.title_match_threshold),
                    paragraph_top_k=int(cfg.paragraph_top_k),
                    max_pairs=int(cfg.max_pairs),
                    use_llm=bool(cfg.use_llm and llm is not None),
                    llm_timeout_s=float(cfg.llm_timeout_s),
                )

                def embed_texts(texts: List[str]):
                    return embedder.embed(texts, batch_size=8, progress_callback=None, progress_every_s=0.0, cancel_event=None)

                runner = CiteCheckRunner(
                    data_dir=str(self.ws.data_dir),
                    embed_texts=embed_texts,
                    model_fingerprint=embedder.model_fingerprint(),
                )

                try:
                    cite_res = runner.run(
                        main_pdf_path=paper_path,
                        papers_root=pdf_root,
                        library_pdf_root=pdf_root,
                        cfg=cfg2,
                        llm=llm,
                        budget=budget,
                        coverage=coverage,
                        cancel_cb=None,
                        progress_cb=progress_cb,
                    )
                except Exception as e:
                    cite_res = {"meta": {"skipped": True, "reason": str(e)[:300]}, "counts": {}, "items": []}
                if isinstance(result, dict):
                    result["citecheck"] = cite_res

        # Attach final LLM usage (per run, not cumulative).
        try:
            if isinstance(result, dict):
                result["llm_usage"] = {
                    "calls": int(budget.calls),
                    "prompt_tokens": int(budget.prompt_tokens),
                    "completion_tokens": int(budget.completion_tokens),
                    "total_tokens": int(budget.total_tokens),
                    "approx_total_tokens": int(budget.approx_total_tokens),
                    "max_total_tokens": int(budget.max_total_tokens),
                    "remaining_tokens": int(budget.budget_remaining_tokens()),
                    "cost_per_1m_tokens": float(budget.cost_per_1m_tokens),
                    "estimated_cost": float(budget.estimated_cost()),
                    "max_cost": float(budget.max_cost),
                    "warnings": list(budget.warnings),
                }
        except Exception:
            pass

        # Persist coverage.
        try:
            coverage.save()
        except Exception:
            pass

        # Export bundle
        export_root = str(self.ws.audit_exports_dir())
        _ensure_dir(export_root)
        if (cfg.export_name or "").strip():
            export_name = (cfg.export_name or "").strip()
        else:
            export_name = f"{_now_slug()}_{Path(paper_path).stem}_vs_{library}"
        export_dir = os.path.join(export_root, export_name)
        _ensure_dir(export_dir)

        from aiwd.report import audit_to_markdown  # type: ignore

        md = audit_to_markdown(result if isinstance(result, dict) else {})
        _dump_json(os.path.join(export_dir, "result.json"), result)
        with open(os.path.join(export_dir, "report.md"), "w", encoding="utf-8") as f:
            f.write(md)

        return export_dir, (result if isinstance(result, dict) else {})
