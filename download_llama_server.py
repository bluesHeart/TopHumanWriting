#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Download a prebuilt llama.cpp Windows binary bundle and extract llama-server.exe.

Default target:
  models/llm/

Notes:
- We copy the entire folder that contains llama-server.exe (including required DLLs)
  into the destination directory to keep the executable runnable.
- Requires internet access at download time.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import zipfile


GITHUB_REPO = "ggerganov/llama.cpp"
DEFAULT_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _request_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ai-words-downloader"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


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


def _pick_asset(assets: list, want: str) -> dict | None:
    want = (want or "").strip().lower()
    if want:
        for a in assets:
            if isinstance(a, dict) and (a.get("name", "") or "").strip().lower() == want:
                return a

    # Prefer CPU x64 for 8GB laptops (no CUDA dependency).
    preferred = [
        "bin-win-cpu-x64.zip",
        "bin-win-cpu-arm64.zip",
        "bin-win-vulkan-x64.zip",
        "bin-win-sycl-x64.zip",
    ]
    for suffix in preferred:
        for a in assets:
            if not isinstance(a, dict):
                continue
            name = (a.get("name", "") or "").strip()
            if name.lower().endswith(suffix):
                return a
    # Fallback: any windows x64 zip.
    for a in assets:
        if not isinstance(a, dict):
            continue
        name = (a.get("name", "") or "").strip().lower()
        if name.endswith(".zip") and "win" in name and "x64" in name:
            return a
    return None


def _find_llama_server_dir(root: str) -> str | None:
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.lower() == "llama-server.exe":
                return base
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=os.path.join("models", "llm"), help="Destination folder (default: models/llm)")
    ap.add_argument("--tag", default="", help="Specific llama.cpp release tag (default: latest)")
    ap.add_argument("--asset", default="", help="Exact asset file name (optional)")
    ap.add_argument("--force", action="store_true", help="Overwrite existing llama-server.exe if present")
    args = ap.parse_args()

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    out_exe = os.path.join(dest, "llama-server.exe")
    if os.path.exists(out_exe) and not args.force:
        print(f"Skip (exists): {out_exe}")
        return 0

    api_url = DEFAULT_API
    if args.tag:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/tags/{args.tag}"

    data = _request_json(api_url)
    tag = (data.get("tag_name", "") or "").strip()
    assets = data.get("assets", [])
    if not isinstance(assets, list) or not assets:
        print("Error: no assets found in release metadata")
        return 2

    asset = _pick_asset(assets, args.asset)
    if not asset:
        print("Error: failed to select a suitable Windows asset")
        return 3

    name = (asset.get("name", "") or "").strip()
    url = (asset.get("browser_download_url", "") or "").strip()
    if not url:
        print(f"Error: missing download url for asset {name}")
        return 4

    zip_path = os.path.join(dest, f"llama.cpp-{tag or 'latest'}.zip")
    _download(url, zip_path)

    with tempfile.TemporaryDirectory(prefix="llama_cpp_") as tmp:
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(tmp)
        except zipfile.BadZipFile:
            print("Error: downloaded file is not a valid zip")
            return 5

        bin_dir = _find_llama_server_dir(tmp)
        if not bin_dir:
            print("Error: llama-server.exe not found in zip")
            return 6

        # Copy the whole binary folder (dlls + exes) into dest.
        for entry in os.listdir(bin_dir):
            src = os.path.join(bin_dir, entry)
            dst = os.path.join(dest, entry)
            try:
                if os.path.isdir(src):
                    # Rare, but keep behavior safe.
                    if os.path.exists(dst) and os.path.isdir(dst):
                        shutil.rmtree(dst, ignore_errors=True)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
            except Exception:
                pass

    print("Done.")
    print(f"Release tag: {tag or 'latest'}")
    print(f"Asset: {name}")
    print(f"llama-server.exe: {os.path.join(dest, 'llama-server.exe')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

