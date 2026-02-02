# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ai_word_detector import SemanticEmbedder, get_app_dir, get_settings_dir  # noqa: E402
from aiwd.citation_bank import CitationBankIndexer  # noqa: E402
from aiwd.llm_budget import LLMBudget  # noqa: E402
from aiwd.llm_review import run_llm_audit_pack  # noqa: E402
from aiwd.materials import MaterialsIndexer, build_material_doc  # noqa: E402
from aiwd.openai_compat import OpenAICompatClient, OpenAICompatConfig  # noqa: E402
from aiwd.rag_index import RagIndexer  # noqa: E402
from aiwd.report import audit_to_markdown  # noqa: E402
from aiwd.review_coverage import ReviewCoverageStore  # noqa: E402


def _load_llm_from_env(*, timeout_s: float = 90.0) -> Optional[OpenAICompatClient]:
    api_key = (os.environ.get("SKILL_LLM_API_KEY", "") or "").strip()
    base_url = (os.environ.get("SKILL_LLM_BASE_URL", "") or "").strip()
    model = (os.environ.get("SKILL_LLM_MODEL", "") or "").strip()
    if not (api_key and base_url and model):
        return None
    cfg = OpenAICompatConfig(api_key=api_key, base_url=base_url, model=model, timeout_s=float(timeout_s))
    return OpenAICompatClient(cfg)


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
    ap = argparse.ArgumentParser(description="Refresh LLM review sections for an existing audit export bundle.")
    ap.add_argument("--export-dir", required=True, help="Path to an export folder containing result.json")
    ap.add_argument(
        "--max-cost",
        "--max-cost-rmb",
        dest="max_cost",
        type=float,
        default=0.0,
        help="Optional max cost estimate (unitless) for this refresh pass (LLM review only).",
    )
    ap.add_argument(
        "--cost-per-1m-tokens",
        "--cost-per-1m-tokens-rmb",
        dest="cost_per_1m_tokens",
        type=float,
        default=0.0,
        help="Optional cost estimate per 1M tokens (unitless).",
    )
    ap.add_argument("--llm-timeout-s", type=float, default=90.0)
    args = ap.parse_args()

    export_dir = os.path.abspath((args.export_dir or "").strip())
    result_path = os.path.join(export_dir, "result.json")
    if not os.path.exists(result_path):
        raise FileNotFoundError(result_path)

    with open(result_path, "r", encoding="utf-8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise RuntimeError("result.json is not an object")

    meta = result.get("meta", {}) if isinstance(result.get("meta", {}), dict) else {}
    paper_path = str(meta.get("paper_pdf_path", "") or "").strip()
    if not paper_path or not os.path.exists(paper_path):
        raise FileNotFoundError(paper_path or "(paper_pdf_path missing)")
    library = str(meta.get("exemplar_library", "") or "").strip() or "reference_papers"
    series_id = str(meta.get("series_id", "") or "").strip() or Path(paper_path).stem

    data_dir = get_settings_dir()
    coverage_dir = os.path.join(data_dir, "audit", "coverage")
    coverage = ReviewCoverageStore.load_or_create(dir_path=coverage_dir, series_id=series_id)

    semantic_dir = os.path.join(get_app_dir(), "models", "semantic")
    if not os.path.exists(semantic_dir):
        raise RuntimeError(f"Missing semantic model folder: {semantic_dir}")
    embedder = SemanticEmbedder(semantic_dir, model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2")

    def embed_query(q: str):
        vecs = embedder.embed([q], batch_size=1, progress_callback=None, progress_every_s=0.0, cancel_event=None)
        try:
            return vecs[0]
        except Exception:
            return vecs

    rag_ix = RagIndexer(data_dir=data_dir, library_name=library)
    rag_sess = rag_ix.create_session(embed_query=embed_query)

    def rag_search(query: str, top_k: int) -> List[Tuple[float, Dict[str, Any]]]:
        hits = rag_sess.search(query, top_k=int(top_k or 8))
        out: List[Tuple[float, Dict[str, Any]]] = []
        for sc, node in hits:
            out.append((float(sc or 0.0), {"pdf": getattr(node, "pdf", "") or "", "page": int(getattr(node, "page", 0) or 0), "text": getattr(node, "text", "") or ""}))
        return out

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

    outlines: List[Dict[str, Any]] = []
    try:
        m = MaterialsIndexer(data_dir=data_dir, library_name=library).load_manifest()
        outlines_raw = m.get("outlines", []) if isinstance(m, dict) else []
        if isinstance(outlines_raw, list):
            outlines = [x for x in outlines_raw if isinstance(x, dict)]
    except Exception:
        outlines = []

    paper_struct = build_material_doc(pdf_path=paper_path, pdf_root=str(Path(paper_path).parent), llm=None)

    llm = _load_llm_from_env(timeout_s=float(args.llm_timeout_s))
    if llm is None:
        raise RuntimeError("LLM env not configured (SKILL_LLM_API_KEY/SKILL_LLM_BASE_URL/SKILL_LLM_MODEL)")

    budget = LLMBudget(cost_per_1m_tokens=float(args.cost_per_1m_tokens), max_cost=float(args.max_cost))

    t0 = time.time()
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
        progress_cb=None,
    )

    new_reviews = pack.get("reviews", {}) if isinstance(pack, dict) else {}
    result["llm_reviews"] = new_reviews

    prev_usage = result.get("llm_usage", {}) if isinstance(result.get("llm_usage", {}), dict) else {}
    pass_usage = _budget_usage(budget)
    result["llm_usage_last_pass"] = pass_usage
    if prev_usage:
        result["llm_usage_prev"] = prev_usage
    # Overwrite llm_usage to reflect THIS refresh pass (so report.md shows the
    # per-run cost, not cumulative across refreshes).
    result["llm_usage"] = pass_usage

    try:
        meta["llm_review_refreshed_at"] = int(time.time())
        meta["llm_review_refresh_seconds"] = float(time.time() - t0)
        result["meta"] = meta
    except Exception:
        pass

    md = audit_to_markdown(result)

    tmp = result_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, result_path)
    with open(os.path.join(export_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    try:
        coverage.save()
    except Exception:
        pass

    print(f"Updated: {export_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
