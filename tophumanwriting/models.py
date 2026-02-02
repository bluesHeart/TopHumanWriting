# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .workspace import Workspace


SEMANTIC_MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
SEMANTIC_BASE_URL = f"https://huggingface.co/{SEMANTIC_MODEL_ID}/resolve/main/"

# remote -> local
SEMANTIC_FILES: Dict[str, str] = {
    "config.json": "config.json",
    "tokenizer.json": "tokenizer.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
    "unigram.json": "unigram.json",
    "onnx/model.onnx": "model.onnx",
}


@dataclass(frozen=True)
class SemanticModelStatus:
    model_id: str
    dir: str
    ok: bool
    missing_files: List[str]
    total_bytes: int


def default_semantic_dir(*, workspace: Optional[Workspace] = None) -> Path:
    ws = workspace or Workspace.from_env()
    return ws.data_dir / "models" / "semantic"


def semantic_model_status(model_dir: Path) -> SemanticModelStatus:
    p = Path(model_dir)
    missing: List[str] = []
    total = 0
    for _remote, local in SEMANTIC_FILES.items():
        fp = p / local
        try:
            if not fp.exists() or fp.stat().st_size <= 0:
                missing.append(local)
            else:
                total += int(fp.stat().st_size)
        except Exception:
            missing.append(local)

    return SemanticModelStatus(
        model_id=SEMANTIC_MODEL_ID,
        dir=str(p),
        ok=(len(missing) == 0),
        missing_files=missing,
        total_bytes=int(total),
    )


def _download_stream(
    *,
    url: str,
    out_path: Path,
    timeout_s: float,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    req = urllib.request.Request(url, headers={"User-Agent": "TopHumanWriting/semantic-model-downloader"})
    with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
        total = int(resp.headers.get("Content-Length", "0") or "0")
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass
    os.replace(tmp, out_path)


def _download_with_retries(
    *,
    url: str,
    out_path: Path,
    timeout_s: float,
    max_retries: int,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Tuple[bool, str]:
    last_err = ""
    tries = max(1, int(max_retries or 0))
    for i in range(tries):
        try:
            _download_stream(url=url, out_path=out_path, timeout_s=timeout_s, progress_cb=progress_cb)
            return True, ""
        except Exception as e:
            last_err = str(e)
            # Exponential-ish backoff (fast for first retries).
            time.sleep(min(8.0, 0.6 * (2**i)))
    return False, last_err


def download_semantic_model(
    *,
    dest_dir: Path,
    force: bool = False,
    timeout_s: float = 120.0,
    max_retries: int = 3,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
) -> SemanticModelStatus:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)

    for remote, local in SEMANTIC_FILES.items():
        out_path = dest / local
        if not force:
            try:
                if out_path.exists() and out_path.stat().st_size > 0:
                    continue
            except Exception:
                pass

        url = SEMANTIC_BASE_URL + remote

        def _p(done: int, total: int) -> None:
            if progress_cb:
                progress_cb(local, int(done), int(total))

        ok, err = _download_with_retries(
            url=url,
            out_path=out_path,
            timeout_s=float(timeout_s),
            max_retries=int(max_retries or 0),
            progress_cb=_p if progress_cb else None,
        )
        if not ok:
            raise RuntimeError(f"Failed to download {local}: {err}")

    return semantic_model_status(dest)

