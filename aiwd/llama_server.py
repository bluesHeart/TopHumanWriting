# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


def _http_json(method: str, url: str, payload: Optional[dict] = None, timeout_s: float = 8.0) -> Tuple[int, dict]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() or b""
            try:
                return status, json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                return status, {}
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
            return int(e.code), json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return int(getattr(e, "code", 500)), {}
    except Exception:
        return 0, {}


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class LlamaServerConfig:
    server_path: str
    model_path: str
    host: str = "127.0.0.1"
    port: int = 0
    ctx_size: int = 2048
    threads: int = 4
    n_gpu_layers: int = 0
    sleep_idle_seconds: int = 300


class LlamaServerProcess:
    def __init__(self, cfg: LlamaServerConfig, *, log_path: Optional[str] = None):
        self.cfg = cfg
        self.log_path = log_path
        self._proc: Optional[subprocess.Popen] = None
        self._model_id: str = ""

    @property
    def base_url(self) -> str:
        port = int(self.cfg.port or 0)
        return f"http://{self.cfg.host}:{port}"

    def is_running(self) -> bool:
        p = self._proc
        return p is not None and p.poll() is None

    def stop(self, timeout_s: float = 2.0):
        p = self._proc
        self._proc = None
        self._model_id = ""
        if p is None:
            return
        try:
            p.terminate()
        except Exception:
            return
        t0 = time.time()
        while time.time() - t0 < float(timeout_s):
            if p.poll() is not None:
                return
            time.sleep(0.05)
        try:
            p.kill()
        except Exception:
            pass

    def health(self, timeout_s: float = 2.0) -> bool:
        status, _ = _http_json("GET", self.base_url + "/health", None, timeout_s=timeout_s)
        return status == 200

    def ensure_started(self, timeout_s: float = 25.0) -> bool:
        if not self.cfg.server_path or not os.path.exists(self.cfg.server_path):
            return False
        if not self.cfg.model_path or not os.path.exists(self.cfg.model_path):
            return False

        if not self.cfg.port:
            self.cfg.port = _find_free_port()

        if self.is_running() and self.health():
            return True

        self.stop()

        args = [
            self.cfg.server_path,
            "-m",
            self.cfg.model_path,
            "-c",
            str(int(self.cfg.ctx_size or 0)),
            "-t",
            str(int(self.cfg.threads or 0)),
            "--host",
            str(self.cfg.host),
            "--port",
            str(int(self.cfg.port)),
            "--sleep-idle-seconds",
            str(int(self.cfg.sleep_idle_seconds or 0)),
        ]
        if int(self.cfg.n_gpu_layers or 0) > 0:
            args.extend(["-ngl", str(int(self.cfg.n_gpu_layers))])

        stdout = stderr = subprocess.DEVNULL
        if self.log_path:
            try:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                f = open(self.log_path, "a", encoding="utf-8")
                stdout = f
                stderr = f
            except Exception:
                stdout = stderr = subprocess.DEVNULL

        try:
            self._proc = subprocess.Popen(args, stdout=stdout, stderr=stderr)
        except Exception:
            self._proc = None
            return False

        t0 = time.time()
        while time.time() - t0 < float(timeout_s):
            if self.health(timeout_s=1.5):
                self._model_id = self._detect_model_id()
                return True
            if self._proc and self._proc.poll() is not None:
                return False
            time.sleep(0.2)
        return False

    def _detect_model_id(self) -> str:
        status, data = _http_json("GET", self.base_url + "/models", None, timeout_s=4.0)
        if status != 200 or not isinstance(data, dict):
            return ""
        items = data.get("data", [])
        if not isinstance(items, list):
            return ""
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = (it.get("id", "") or "").strip()
            path = (it.get("path", "") or "").strip()
            if self.cfg.model_path and path and os.path.abspath(path) == os.path.abspath(self.cfg.model_path):
                return mid
        # Fallback to first id.
        for it in items:
            if isinstance(it, dict):
                mid = (it.get("id", "") or "").strip()
                if mid:
                    return mid
        return ""

    def chat_completions(self, payload: dict, timeout_s: float = 60.0) -> Tuple[int, dict]:
        return _http_json("POST", self.base_url + "/v1/chat/completions", payload, timeout_s=timeout_s)

    def chat(
        self,
        *,
        messages: list,
        temperature: float = 0.1,
        max_tokens: int = 320,
        response_format: Optional[dict] = None,
        timeout_s: float = 90.0,
    ) -> Tuple[int, dict]:
        model = self._model_id or ""
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(temperature or 0.0),
            "max_tokens": int(max_tokens or 0),
        }
        if isinstance(response_format, dict) and response_format:
            payload["response_format"] = response_format

        status, data = self.chat_completions(payload, timeout_s=timeout_s)
        # Some llama.cpp builds may not support response_format; retry without it.
        if int(status or 0) in (400, 404, 422) and "response_format" in payload:
            payload.pop("response_format", None)
            status2, data2 = self.chat_completions(payload, timeout_s=timeout_s)
            if int(status2 or 0) != 0:
                return status2, data2
        return status, data
