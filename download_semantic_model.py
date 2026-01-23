#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Download the offline multilingual semantic model (ONNX) used for sentence similarity retrieval.

Default destination:
  models/semantic/
"""

import os
import sys
import urllib.request


MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
BASE_URL = f"https://huggingface.co/{MODEL_ID}/resolve/main/"

# remote -> local
FILES = {
    "config.json": "config.json",
    "tokenizer.json": "tokenizer.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
    "unigram.json": "unigram.json",
    "onnx/model.onnx": "model.onnx",
}


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


def main():
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join("models", "semantic")
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)

    print(f"Model: {MODEL_ID}")
    print(f"Destination: {dest}")

    for remote, local in FILES.items():
        out_path = os.path.join(dest, local)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"Skip (exists): {local}")
            continue
        _download(BASE_URL + remote, out_path)

    print("Done.")


if __name__ == "__main__":
    main()

