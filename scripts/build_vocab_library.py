# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT_DIR = str(Path(__file__).resolve().parent.parent)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from ai_word_detector import AcademicCorpus, LibraryManager, get_settings_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Build exemplar vocabulary stats (word doc frequency) for a PDF library.")
    ap.add_argument("--library", default="reference_papers", help="Library name (writes to TopHumanWriting_data/libraries/<name>.json)")
    ap.add_argument("--pdf-root", default="", help="PDF folder path (default: ./reference_papers)")
    args = ap.parse_args()

    library = (args.library or "").strip()
    if not library:
        raise ValueError("library required")

    pdf_root = (args.pdf_root or "").strip()
    if not pdf_root:
        pdf_root = os.path.join(ROOT_DIR, "reference_papers")
    pdf_root = os.path.abspath(pdf_root)
    if not os.path.exists(pdf_root):
        raise FileNotFoundError(pdf_root)

    lm = LibraryManager()
    lib_path = lm.get_library_path(library)
    os.makedirs(os.path.dirname(lib_path), exist_ok=True)

    corpus = AcademicCorpus(lib_path)

    def prog(done: int, total: int, detail: str = ""):
        try:
            pct = int(max(0.0, min(1.0, float(done) / float(max(1, int(total))))) * 100)
        except Exception:
            pct = 0
        d = (detail or "").replace("\n", " ").strip()
        if len(d) > 80:
            d = "…" + d[-79:]
        print(f"[{pct:3d}%] {done}/{total} {d}")

    t0 = time.time()
    count = corpus.process_pdf_folder(
        pdf_root,
        prog,
        semantic_embedder=None,
        semantic_progress_callback=None,
        syntax_analyzer=None,
        syntax_progress_callback=None,
        cancel_event=None,
    )
    corpus.save_vocabulary()
    dt = time.time() - t0

    print("")
    print(f"Built vocab library: {library}")
    print(f"PDF root: {pdf_root}")
    print(f"Output: {lib_path}")
    print(f"Docs: {count}")
    print(f"Seconds: {dt:.1f}")
    print(f"Data dir: {get_settings_dir()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

