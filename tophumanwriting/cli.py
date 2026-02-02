# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from dataclasses import asdict
from typing import Optional

from .api import PROFILES, TopHumanWriting
from .library import LibraryBuildConfig, LibraryBuilder
from .runner import AuditRunConfig, AuditRunner
from .workspace import Workspace


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _cmd_library_status(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    b = LibraryBuilder(ws)
    st = b.status(name=str(args.name))
    _print_json(asdict(st))
    return 0


def _cmd_library_build(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    b = LibraryBuilder(ws)
    cfg = LibraryBuildConfig(
        name=str(args.name),
        pdf_root=str(args.pdf_root),
        semantic_model_dir=str(args.semantic_model_dir or "") or None,
        build_rag=bool(args.with_rag),
        build_cite=bool(args.with_cite),
        build_materials=bool(args.with_materials),
        build_vocab=bool(args.with_vocab),
        force_rebuild=bool(args.force),
        materials_use_llm=bool(args.materials_use_llm),
    )

    def prog(stage: str, done: int, total: int, detail: str):
        s = str(stage or "").strip()
        d = str(detail or "").replace("\n", " ").strip()
        if total > 0:
            try:
                pct = int(max(0.0, min(1.0, float(done) / float(total))) * 100)
            except Exception:
                pct = 0
            print(f"[{pct:3d}%] {s}: {d}")
        else:
            print(f"[---] {s}: {d}")

    st = b.build(cfg, progress_cb=prog)
    _print_json(asdict(st))
    return 0


def _cmd_audit_run(args: argparse.Namespace) -> int:
    ws = Workspace.from_env()
    r = AuditRunner(ws)
    cfg = AuditRunConfig(
        paper_pdf_path=str(args.paper),
        exemplar_library=str(args.library),
        series_id=str(args.series_id or ""),
        top_k=int(args.top_k),
        max_pages=int(args.max_pages),
        max_sentences=int(args.max_sentences),
        min_sentence_len=int(args.min_sentence_len),
        low_alignment_threshold=float(args.low_alignment_threshold),
        include_citecheck=bool(args.citecheck),
        references_pdf_root=str(args.references_pdf_root or ""),
        title_match_threshold=float(args.title_match_threshold),
        paragraph_top_k=int(args.paragraph_top_k),
        max_pairs=int(args.max_pairs),
        use_llm=bool(args.use_llm),
        max_llm_tokens=int(args.max_llm_tokens),
        cost_per_1m_tokens=float(args.cost_per_1m_tokens),
        max_cost=float(args.max_cost),
        llm_timeout_s=float(args.llm_timeout_s),
        export_name=str(args.export_name or ""),
    )

    def prog(stage: str, done: int, total: int, detail: str):
        s = str(stage or "").strip()
        d = str(detail or "").replace("\n", " ").strip()
        if total > 0:
            try:
                pct = int(max(0.0, min(1.0, float(done) / float(total))) * 100)
            except Exception:
                pct = 0
            print(f"[{pct:3d}%] {s}: {d}")
        else:
            print(f"[---] {s}: {d}")

    export_dir, _result = r.run(cfg, progress_cb=prog)
    print("")
    print(f"Export written to: {export_dir}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cfg = dict(
        exemplars=str(args.exemplars),
        library_name=str(args.library_name or ""),
        data_dir=str(args.data_dir or ""),
        rag_backend=str(args.rag_backend or "auto"),
        semantic_model_dir=str(args.semantic_model_dir or ""),
        auto_download_semantic=not bool(args.no_download_models),
    )
    thw = TopHumanWriting(**cfg)

    def prog(stage: str, done: int, total: int, detail: str):
        s = str(stage or "").strip()
        d = str(detail or "").replace("\n", " ").strip()
        if total > 0:
            try:
                pct = int(max(0.0, min(1.0, float(done) / float(total))) * 100)
            except Exception:
                pct = 0
            print(f"[{pct:3d}%] {s}: {d}")
        else:
            print(f"[---] {s}: {d}")

    export = thw.run(
        str(args.paper),
        profile=str(args.profile or "standard"),
        max_llm_tokens=int(args.max_llm_tokens),
        max_cost=float(args.max_cost),
        cost_per_1m_tokens=float(args.cost_per_1m_tokens),
        max_pages=int(args.max_pages),
        use_llm=bool(args.use_llm),
        progress=prog,
    )
    print("")
    print(f"Export written to: {export.export_dir}")
    return 0


def _cmd_models_status(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .models import default_semantic_dir, semantic_model_status

    ws = Workspace.from_env()
    semantic_dir = str(args.semantic_dir or "").strip()
    p = Path(semantic_dir) if semantic_dir else default_semantic_dir(workspace=ws)
    st = semantic_model_status(p)
    _print_json(asdict(st))
    return 0


def _cmd_models_download_semantic(args: argparse.Namespace) -> int:
    import time
    from pathlib import Path

    from .models import default_semantic_dir, download_semantic_model

    ws = Workspace.from_env()
    dest = str(args.dest or "").strip()
    dest_dir = Path(dest) if dest else default_semantic_dir(workspace=ws)

    last: dict[str, object] = {"file": "", "pct": -1, "t": 0.0}

    def prog(file: str, done: int, total: int) -> None:
        try:
            now = float(time.time())
            f = str(file or "").strip()
            pct = int(max(0.0, min(1.0, float(done) / float(total))) * 100) if total > 0 else 0
            if f != str(last.get("file", "")):
                print(f"[---] semantic_model: {f}")
                last["file"] = f
                last["pct"] = -1
                last["t"] = 0.0
            if pct >= int(last.get("pct", -1) or -1) + 5 or (now - float(last.get("t", 0.0) or 0.0)) > 0.8 or done == total:
                last["pct"] = pct
                last["t"] = now
                if total > 0:
                    print(f"[{pct:3d}%] semantic_model: {f}")
        except Exception:
            return

    st = download_semantic_model(
        dest_dir=dest_dir,
        force=bool(args.force),
        timeout_s=float(args.timeout_s),
        max_retries=int(args.retries),
        progress_cb=prog,
    )
    _print_json(asdict(st))
    return 0


def _cmd_llm_test(args: argparse.Namespace) -> int:
    from aiwd.openai_compat import OpenAICompatClient, OpenAICompatConfig, extract_first_content, extract_usage, mask_secret  # type: ignore
    from .runner import resolve_llm_config

    cfg0 = resolve_llm_config()
    api_key = cfg0.api_key
    base_url = cfg0.base_url
    model = cfg0.model

    if cfg0.source == "missing":
        print("Missing LLM config. Set env TOPHUMANWRITING_LLM_* or configure TopHumanWriting_data/settings.json (llm_api_*).")
        return 2

    cfg = OpenAICompatConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_s=float(args.timeout_s),
        max_retries=int(args.retries),
        base_retry_delay_s=0.9,
        max_retry_delay_s=10.0,
    )
    cli = OpenAICompatClient(cfg)

    print(f"base_url: {cfg.base_url_v1}")
    print(f"model:    {cfg.model}")
    print(f"api_key:  {mask_secret(cfg.api_key)}")
    print(f"source:   {cfg0.source}")
    print("")

    messages = [
        {"role": "system", "content": "You are a minimal connectivity test. Reply with exactly: ok"},
        {"role": "user", "content": str(args.prompt)},
    ]
    status, resp = cli.chat(
        messages=messages,
        temperature=0.0,
        max_tokens=int(args.max_tokens),
        response_format=None,
        timeout_s=float(args.timeout_s),
    )
    content = extract_first_content(resp if isinstance(resp, dict) else {})
    usage = extract_usage(resp if isinstance(resp, dict) else {})

    _print_json(
        {
            "http_status": int(status or 0),
            "content": content,
            "usage": usage,
            "error": (resp or {}).get("error") if isinstance(resp, dict) else None,
            "raw_error": (resp or {}).get("_error") if isinstance(resp, dict) else None,
            "raw_body": (resp or {}).get("_raw") if isinstance(resp, dict) else None,
        }
    )
    return 0 if int(status or 0) == 200 else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="thw", description="TopHumanWriting CLI (library build + paper audit).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    # run (one command UX)
    sp_runall = sub.add_parser("run", help="One command: build library if needed + audit a paper.")
    sp_runall.add_argument("--paper", required=True, help="Target PDF path (text-based)")
    sp_runall.add_argument("--exemplars", required=True, help="Folder containing exemplar PDFs")
    sp_runall.add_argument("--library-name", default="", help="Artifact name (default: derived from exemplars folder name)")
    sp_runall.add_argument("--data-dir", default="", help="Workspace data dir (default: TopHumanWriting_data next to repo)")
    sp_runall.add_argument("--profile", default="standard", choices=sorted(PROFILES.keys()), help="Audit profile")
    sp_runall.add_argument("--rag-backend", default="auto", choices=["auto", "chroma", "faiss"], help="RAG backend")
    sp_runall.add_argument("--semantic-model-dir", default="", help="Override semantic embedder model dir")
    sp_runall.add_argument("--no-download-models", action="store_true", help="Do not auto-download missing semantic model")
    sp_runall.add_argument("--max-pages", type=int, default=0)
    sp_runall.add_argument("--use-llm", action="store_true", default=True)
    sp_runall.add_argument("--no-llm", dest="use_llm", action="store_false")
    sp_runall.add_argument("--max-llm-tokens", type=int, default=200_000, help="Token budget (recommended)")
    sp_runall.add_argument("--cost-per-1m-tokens", type=float, default=0.0, dest="cost_per_1m_tokens", help="Optional cost estimate per 1M tokens (unitless)")
    sp_runall.add_argument("--max-cost", type=float, default=0.0, dest="max_cost", help="Optional max cost estimate (unitless)")
    sp_runall.set_defaults(func=_cmd_run)

    # library
    sp_lib = sub.add_parser("library", help="Manage exemplar libraries (build once, reuse).")
    sub_lib = sp_lib.add_subparsers(dest="lib_cmd", required=True)

    sp_ls = sub_lib.add_parser("status", help="Show artifact readiness for a library.")
    sp_ls.add_argument("--name", required=True, help="Library name (e.g., reference_papers)")
    sp_ls.set_defaults(func=_cmd_library_status)

    sp_b = sub_lib.add_parser("build", help="Build library artifacts (RAG/cite/materials/vocab).")
    sp_b.add_argument("--name", required=True, help="Library name (artifact namespace)")
    sp_b.add_argument("--pdf-root", required=True, help="Folder containing PDFs")
    sp_b.add_argument("--semantic-model-dir", default="", help="Local ONNX embedding model dir (default: resolve automatically)")
    sp_b.add_argument("--force", action="store_true", help="Force rebuild even if artifacts already exist")
    sp_b.add_argument("--with-rag", action="store_true", default=True)
    sp_b.add_argument("--no-rag", dest="with_rag", action="store_false")
    sp_b.add_argument("--with-cite", action="store_true", default=True)
    sp_b.add_argument("--no-cite", dest="with_cite", action="store_false")
    sp_b.add_argument("--with-materials", action="store_true", default=True)
    sp_b.add_argument("--no-materials", dest="with_materials", action="store_false")
    sp_b.add_argument("--with-vocab", action="store_true", default=True)
    sp_b.add_argument("--no-vocab", dest="with_vocab", action="store_false")
    sp_b.add_argument("--materials-use-llm", action="store_true", default=False, help="Allow LLM for materials extraction (default off)")
    sp_b.set_defaults(func=_cmd_library_build)

    # audit
    sp_audit = sub.add_parser("audit", help="Run audits against an exemplar library.")
    sub_a = sp_audit.add_subparsers(dest="audit_cmd", required=True)

    sp_run = sub_a.add_parser("run", help="Run a full audit and write an export bundle.")
    sp_run.add_argument("--paper", required=True, help="Target PDF path (text-based)")
    sp_run.add_argument("--library", default="reference_papers", help="Exemplar library name")
    sp_run.add_argument("--series-id", default="", help="Coverage series id (same id avoids re-checking unchanged text)")
    sp_run.add_argument("--top-k", type=int, default=20)
    sp_run.add_argument("--max-pages", type=int, default=0)
    sp_run.add_argument("--max-sentences", type=int, default=3600)
    sp_run.add_argument("--min-sentence-len", type=int, default=20)
    sp_run.add_argument("--low-alignment-threshold", type=float, default=0.35)
    sp_run.add_argument("--citecheck", action="store_true", default=True)
    sp_run.add_argument("--no-citecheck", dest="citecheck", action="store_false")
    sp_run.add_argument("--references-pdf-root", default="", help="PDF root for CiteCheck (default: library pdf_root)")
    sp_run.add_argument("--title-match-threshold", type=float, default=0.55)
    sp_run.add_argument("--paragraph-top-k", type=int, default=20)
    sp_run.add_argument("--max-pairs", type=int, default=800)
    sp_run.add_argument("--use-llm", action="store_true", default=True)
    sp_run.add_argument("--no-llm", dest="use_llm", action="store_false")
    sp_run.add_argument("--max-llm-tokens", type=int, default=200_000, help="Token budget (recommended)")
    sp_run.add_argument("--cost-per-1m-tokens", type=float, default=0.0, dest="cost_per_1m_tokens", help="Optional cost estimate per 1M tokens (unitless)")
    sp_run.add_argument("--max-cost", type=float, default=0.0, dest="max_cost", help="Optional max cost estimate (unitless)")
    sp_run.add_argument("--llm-timeout-s", type=float, default=90.0)
    sp_run.add_argument("--export-name", default="", help="Export folder name (default auto)")
    sp_run.set_defaults(func=_cmd_audit_run)

    # models
    sp_models = sub.add_parser("models", help="Manage offline models (semantic embeddings).")
    sub_m = sp_models.add_subparsers(dest="models_cmd", required=True)

    sp_ms = sub_m.add_parser("status", help="Show semantic embedding model status.")
    sp_ms.add_argument("--semantic-dir", default="", help="Override semantic model dir (default: <data_dir>/models/semantic)")
    sp_ms.set_defaults(func=_cmd_models_status)

    sp_dl = sub_m.add_parser("download-semantic", help="Download semantic embedding model (ONNX) to a writable dir.")
    sp_dl.add_argument("--dest", default="", help="Destination dir (default: <data_dir>/models/semantic)")
    sp_dl.add_argument("--force", action="store_true", help="Redownload even if files already exist")
    sp_dl.add_argument("--timeout-s", type=float, default=180.0)
    sp_dl.add_argument("--retries", type=int, default=3)
    sp_dl.set_defaults(func=_cmd_models_download_semantic)

    # llm
    sp_llm = sub.add_parser("llm", help="Test OpenAI-compatible LLM API connectivity.")
    sub_llm = sp_llm.add_subparsers(dest="llm_cmd", required=True)

    sp_test = sub_llm.add_parser("test", help="Send a minimal request and print response + token usage (best-effort).")
    sp_test.add_argument("--prompt", default="ok", help="User prompt for the test request")
    sp_test.add_argument("--max-tokens", type=int, default=64)
    sp_test.add_argument("--timeout-s", type=float, default=60.0)
    sp_test.add_argument("--retries", type=int, default=5)
    sp_test.set_defaults(func=_cmd_llm_test)

    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        ap.print_help()
        return 2
    try:
        return int(fn(args) or 0)
    except KeyboardInterrupt:
        print("Canceled.")
        return 130
    except Exception as e:
        msg = str(e or "").strip() or e.__class__.__name__
        print(f"Error: {msg}")
        if (os.environ.get("TOPHUMANWRITING_DEBUG", "") or "").strip():
            traceback.print_exc()
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
