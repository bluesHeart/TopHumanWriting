#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Download the default 3B GGUF model used by AI Word Detector (offline polish).

Default model:
  Qwen/Qwen2.5-3B-Instruct-GGUF
  qwen2.5-3b-instruct-q4_k_m.gguf

Default destination:
  models/llm/
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request


DEFAULT_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
DEFAULT_FILE = "qwen2.5-3b-instruct-q4_k_m.gguf"


def _download(url: str, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    print(f"Downloading {url}")

    def report(block_num, block_size, total_size):
        if total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = min(100.0, downloaded * 100.0 / total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        sys.stdout.write(f"\r  {pct:5.1f}%  {mb:,.1f}MB / {total_mb:,.1f}MB")
        sys.stdout.flush()

    urllib.request.urlretrieve(url, tmp, reporthook=report)
    sys.stdout.write("\n")
    sys.stdout.flush()
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=os.path.join("models", "llm"), help="Destination folder (default: models/llm)")
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"Hugging Face repo id (default: {DEFAULT_REPO})")
    ap.add_argument("--file", default=DEFAULT_FILE, help=f"GGUF filename in the repo (default: {DEFAULT_FILE})")
    ap.add_argument("--force", action="store_true", help="Overwrite if target file exists")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)

    filename = (args.file or "").strip()
    if not filename:
        print("Error: empty --file")
        return 2

    out_path = os.path.join(dest, filename)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0 and not args.force:
        print(f"Skip (exists): {out_path}")
        return 0

    repo = (args.repo or "").strip()
    if not repo or "/" not in repo:
        print("Error: invalid --repo (expected owner/name)")
        return 3

    base_url = f"https://huggingface.co/{repo}/resolve/main/{filename}"
    _download(base_url, out_path)
    print("Done.")
    print(f"Model: {repo}")
    print(f"File: {filename}")
    print(f"Path: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
