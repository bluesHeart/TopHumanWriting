# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ai_word_detector import AcademicCorpus, LanguageDetector, LibraryManager  # noqa: E402
from aiwd.audit import analyze_lexical_stats, extract_pdf_pages_text, guess_language_for_sentence  # noqa: E402
from aiwd.report import audit_to_markdown  # noqa: E402
from ai_word_detector import split_sentences_with_positions  # noqa: E402


def _dump_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _extract_sentences_for_lexical(pdf_path: str) -> List[Dict[str, Any]]:
    pages = extract_pdf_pages_text(pdf_path, max_pages=None)
    full_head = "\n".join([p.get("text", "") or "" for p in pages[: min(3, len(pages))]])
    primary_lang = "en"
    try:
        primary_lang = LanguageDetector.detect(full_head or "")
    except Exception:
        primary_lang = "en"
    if primary_lang not in ("en", "zh", "mixed"):
        primary_lang = "en"

    sentences: List[Dict[str, Any]] = []
    sid = 0
    for p in pages:
        page = int(p.get("page", 0) or 0)
        txt = str(p.get("text", "") or "")
        if not txt.strip():
            continue
        lang = primary_lang
        if primary_lang == "mixed":
            try:
                lang = LanguageDetector.detect(txt) or "mixed"
            except Exception:
                lang = "mixed"
            if lang not in ("en", "zh", "mixed"):
                lang = "mixed"
        try:
            sents = split_sentences_with_positions(txt, lang)
        except Exception:
            sents = []
        for sent, _s, _e in sents:
            st = (sent or "").strip()
            if not st:
                continue
            sentences.append(
                {
                    "id": sid,
                    "page": page,
                    "text": st,
                    "lang": guess_language_for_sentence(st, fallback=lang if lang != "mixed" else "en"),
                }
            )
            sid += 1
    return sentences


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh lexical stats for an existing audit export bundle.")
    ap.add_argument("--export-dir", required=True, help="Path to an export folder containing result.json")
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
    library = str(meta.get("exemplar_library", "") or "").strip()
    if not library:
        raise RuntimeError("meta.exemplar_library missing")

    lm = LibraryManager()
    lib_path = lm.get_library_path(library)
    corpus = AcademicCorpus(lib_path)
    if not corpus.load_vocabulary():
        raise RuntimeError(f"Missing vocabulary library: {lib_path}. Run scripts/build_vocab_library.py first.")

    t0 = time.time()
    sentences = _extract_sentences_for_lexical(paper_path)
    lexical = analyze_lexical_stats(sentences, corpus=corpus)
    result["lexical"] = lexical
    try:
        meta["lexical_refreshed_at"] = int(time.time())
        meta["lexical_refresh_seconds"] = float(time.time() - t0)
        result["meta"] = meta
    except Exception:
        pass

    md = audit_to_markdown(result)
    _dump_json(result_path, result)
    with open(os.path.join(export_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Updated: {export_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

