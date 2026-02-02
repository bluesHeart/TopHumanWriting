# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ai_word_detector import AcademicCorpus, LibraryManager, SemanticEmbedder, get_app_dir, get_settings_dir
from aiwd.audit import run_full_paper_audit
from aiwd.citation_bank import CitationBankIndexer
from aiwd.cite_check import CiteCheckConfig, CiteCheckRunner
from aiwd.llm_budget import LLMBudget
from aiwd.llm_review import run_llm_audit_pack
from aiwd.materials import MaterialsIndexer, build_material_doc
from aiwd.openai_compat import OpenAICompatClient, OpenAICompatConfig
from aiwd.rag_index import RagIndexer
from aiwd.report import audit_to_markdown
from aiwd.review_coverage import ReviewCoverageStore


def _now_slug() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_llm_from_env(*, timeout_s: float = 90.0) -> Optional[OpenAICompatClient]:
    api_key = (os.environ.get("SKILL_LLM_API_KEY", "") or "").strip()
    base_url = (os.environ.get("SKILL_LLM_BASE_URL", "") or "").strip()
    model = (os.environ.get("SKILL_LLM_MODEL", "") or "").strip()
    if not (api_key and base_url and model):
        return None
    cfg = OpenAICompatConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=float(timeout_s),
        max_retries=5,
        base_retry_delay_s=0.9,
        max_retry_delay_s=10.0,
    )
    return OpenAICompatClient(cfg)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _dump_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _budget_usage(budget: LLMBudget) -> dict:
    return {
        "calls": int(budget.calls),
        "prompt_tokens": int(budget.prompt_tokens),
        "completion_tokens": int(budget.completion_tokens),
        "total_tokens": int(budget.total_tokens),
        "approx_total_tokens": int(budget.approx_total_tokens),
        "cost_per_1m_tokens": float(getattr(budget, "cost_per_1m_tokens", 0.0) or 0.0),
        "estimated_cost": float(getattr(budget, "estimated_cost", lambda: 0.0)() or 0.0),
        "max_cost": float(getattr(budget, "max_cost", 0.0) or 0.0),
        "warnings": list(budget.warnings),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="TopHumanWriting: run an end-to-end audit and export a report bundle.")
    ap.add_argument("--paper", default="main.pdf", help="Path to target PDF (text-based).")
    ap.add_argument("--library", default="reference_papers", help="Exemplar library name (RAG/Cite/Materials must exist).")
    ap.add_argument("--series-id", default="", help="Coverage series id. Same id avoids re-checking on subsequent runs.")
    ap.add_argument("--top-k", type=int, default=20, help="RAG top_k for alignment and evidence.")
    ap.add_argument("--max-pages", type=int, default=0, help="Max pages to audit (0 = no limit).")
    ap.add_argument("--max-sentences", type=int, default=3600, help="Max sampled sentences for alignment scoring.")
    ap.add_argument("--min-sentence-len", type=int, default=20, help="Min sentence length to score.")
    ap.add_argument("--low-alignment-threshold", type=float, default=0.35, help="Threshold for low-alignment flag.")
    ap.add_argument("--include-citecheck", action="store_true", default=True, help="Run CiteCheck (default: on).")
    ap.add_argument("--no-citecheck", dest="include_citecheck", action="store_false", help="Disable CiteCheck.")
    ap.add_argument("--use-llm", action="store_true", default=True, help="Enable LLM (review + citecheck) (default: on).")
    ap.add_argument("--no-llm", dest="use_llm", action="store_false", help="Disable LLM (fallback to non-LLM checks only).")
    ap.add_argument(
        "--cost-per-1m-tokens",
        "--cost-per-1m-tokens-rmb",
        dest="cost_per_1m_tokens",
        type=float,
        default=0.0,
        help="Optional cost estimate per 1M tokens (unitless).",
    )
    ap.add_argument(
        "--max-cost",
        "--max-cost-rmb",
        dest="max_cost",
        type=float,
        default=0.0,
        help="Optional max cost estimate (unitless).",
    )
    ap.add_argument("--llm-timeout-s", type=float, default=90.0, help="Timeout per LLM call.")
    ap.add_argument("--title-match-threshold", type=float, default=0.55, help="CiteCheck title match threshold.")
    ap.add_argument("--paragraph-top-k", type=int, default=20, help="CiteCheck paragraph retrieval top_k.")
    ap.add_argument("--max-pairs", type=int, default=800, help="Max cite pairs to check.")
    ap.add_argument("--export-name", default="", help="Export folder name (default auto).")
    args = ap.parse_args()

    paper_path = os.path.abspath((args.paper or "").strip())
    if not paper_path or not os.path.exists(paper_path):
        raise FileNotFoundError(paper_path)

    library = (args.library or "").strip()
    if not library:
        raise ValueError("library required")

    series_id = (args.series_id or "").strip() or Path(paper_path).stem

    data_dir = get_settings_dir()
    coverage_dir = os.path.join(data_dir, "audit", "coverage")
    coverage = ReviewCoverageStore.load_or_create(dir_path=coverage_dir, series_id=series_id)

    semantic_dir = os.path.join(get_app_dir(), "models", "semantic")
    if not os.path.exists(semantic_dir):
        raise RuntimeError(f"Missing semantic model folder: {semantic_dir}")
    embedder = SemanticEmbedder(semantic_dir, model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2")

    # Load exemplar vocabulary (word doc frequency) when available, to enable lexical stats.
    corpus = None
    try:
        lib_path = LibraryManager().get_library_path(library)
        c0 = AcademicCorpus(lib_path)
        if c0.load_vocabulary():
            corpus = c0
        else:
            print(f"[warn] missing vocabulary library: {lib_path} (run scripts/build_vocab_library.py)")
    except Exception:
        corpus = None

    def embed_query(q: str):
        vecs = embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
        try:
            return vecs[0]
        except Exception:
            return vecs

    # Exemplar RAG search session (FAISS)
    rag_ix = RagIndexer(data_dir=data_dir, library_name=library)
    try:
        rag_sess = rag_ix.create_session(embed_query=embed_query)
    except Exception as e:
        raise RuntimeError(f"RAG index missing/broken for library: {library} ({e})") from e

    def rag_search(query: str, top_k: int) -> List[Tuple[float, Dict[str, Any]]]:
        hits = rag_sess.search(query, top_k=int(top_k or 8))
        out: List[Tuple[float, Dict[str, Any]]] = []
        for sc, node in hits:
            out.append((float(sc or 0.0), {"pdf": getattr(node, "pdf", "") or "", "page": int(getattr(node, "page", 0) or 0), "text": getattr(node, "text", "") or ""}))
        return out

    # Exemplar outline templates (from materials manifest)
    outlines: List[Dict[str, Any]] = []
    materials_manifest: dict = {}
    try:
        mat_ix = MaterialsIndexer(data_dir=data_dir, library_name=library)
        materials_manifest = mat_ix.load_manifest()
        outlines_raw = materials_manifest.get("outlines", [])
        if isinstance(outlines_raw, list):
            outlines = [x for x in outlines_raw if isinstance(x, dict)]
    except Exception:
        outlines = []

    # Citation-style exemplar search session (FAISS)
    cite_search_fn = None
    cite_ix = CitationBankIndexer(data_dir=data_dir, library_name=library)
    if cite_ix.index_ready():
        cite_sess = cite_ix.create_session(embed_query=embed_query)

        def _cite_search(query: str, top_k: int) -> List[Tuple[float, Dict[str, Any]]]:
            hits = cite_sess.search(query, top_k=int(top_k or 8))
            out: List[Tuple[float, Dict[str, Any]]] = []
            for h in hits:
                out.append(
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
            return out

        cite_search_fn = _cite_search

    # LLM client + shared budget
    llm: Optional[OpenAICompatClient] = _load_llm_from_env(timeout_s=float(args.llm_timeout_s)) if bool(args.use_llm) else None
    budget = LLMBudget(cost_per_1m_tokens=float(args.cost_per_1m_tokens), max_cost=float(args.max_cost))

    def progress(stage: str, done: int, total: int, detail: str):
        st = (stage or "").strip()
        if total > 0:
            pct = int(max(0.0, min(1.0, float(done) / float(total))) * 100)
            print(f"[{pct:3d}%] {st}: {detail}")
        else:
            print(f"[---] {st}: {detail}")

    t0 = time.time()
    max_pages = None
    if int(args.max_pages or 0) > 0:
        max_pages = int(args.max_pages)

    result = run_full_paper_audit(
        paper_pdf_path=paper_path,
        exemplar_library=library,
        search_exemplars=lambda q, k: rag_search(q, int(k)),
        corpus=corpus,
        syntax_analyzer=None,
        max_pages=max_pages,
        max_sentences=int(args.max_sentences),
        min_sentence_len=int(args.min_sentence_len),
        top_k=int(args.top_k),
        low_alignment_threshold=float(args.low_alignment_threshold),
        include_style=True,
        include_repetition=True,
        include_syntax=True,
        cancel_cb=None,
        progress_cb=progress,
    )
    try:
        if isinstance(result.get("meta", {}), dict):
            result["meta"]["series_id"] = series_id
            result["meta"]["elapsed_s"] = float(time.time() - t0)
    except Exception:
        pass

    # Paper structure (paragraphs/headings/citation sentences)
    try:
        paper_struct = build_material_doc(pdf_path=paper_path, pdf_root=str(Path(paper_path).parent), llm=None)
    except Exception:
        paper_struct = {}

    # LLM reviews (sentence/paragraph/outline/citation style)
    if llm is not None:
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
                cost_per_1m_tokens=float(args.cost_per_1m_tokens),
                max_cost=float(args.max_cost),
                progress_cb=progress,
            )
            if isinstance(result, dict):
                result["llm_reviews"] = pack.get("reviews", {}) if isinstance(pack, dict) else {}
        except Exception as e:
            if isinstance(result, dict):
                result["llm_reviews"] = {"skipped": True, "reason": str(e)[:300]}

    # CiteCheck (shared budget)
    pdf_root = ""
    try:
        pdf_root = str(materials_manifest.get("pdf_root", "") or "").strip()
    except Exception:
        pdf_root = ""
    if pdf_root:
        pdf_root = pdf_root.replace("/", os.sep)
    if not pdf_root:
        pdf_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference_papers")
        pdf_root = os.path.abspath(pdf_root)
    if bool(args.include_citecheck) and os.path.exists(pdf_root):
        try:
            cfg = CiteCheckConfig(
                title_match_threshold=float(args.title_match_threshold),
                paragraph_top_k=int(args.paragraph_top_k),
                max_pairs=int(args.max_pairs),
                use_llm=bool(args.use_llm and llm is not None),
                llm_timeout_s=float(args.llm_timeout_s),
            )

            def embed_texts(texts: List[str]):
                return embedder.embed(texts, batch_size=8, progress_callback=None, progress_every_s=0.0, cancel_event=None)

            runner = CiteCheckRunner(
                data_dir=data_dir,
                embed_texts=embed_texts,
                model_fingerprint=embedder.model_fingerprint(),
            )
            cite_res = runner.run(
                main_pdf_path=paper_path,
                papers_root=pdf_root,
                library_pdf_root=pdf_root,
                cfg=cfg,
                llm=llm,
                budget=budget,
                coverage=coverage,
                cancel_cb=None,
                progress_cb=progress,
            )
            if isinstance(result, dict):
                result["citecheck"] = cite_res
        except Exception as e:
            if isinstance(result, dict):
                result["citecheck"] = {"meta": {"skipped": True, "reason": str(e)[:300]}, "counts": {}, "items": []}

    if isinstance(result, dict):
        result["llm_usage"] = _budget_usage(budget)

    md = audit_to_markdown(result if isinstance(result, dict) else {})

    export_root = os.path.join(data_dir, "audit", "exports")
    _ensure_dir(export_root)
    if (args.export_name or "").strip():
        export_name = (args.export_name or "").strip()
    else:
        export_name = f"{_now_slug()}_{Path(paper_path).stem}_vs_{library}"
    export_dir = os.path.join(export_root, export_name)
    _ensure_dir(export_dir)

    _dump_json(os.path.join(export_dir, "result.json"), result)
    with open(os.path.join(export_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    try:
        coverage.save()
    except Exception:
        pass

    print("")
    print(f"Export written to: {export_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
