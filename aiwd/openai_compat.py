# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


def mask_secret(value: str, *, show_last: int = 4) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= int(show_last):
        return "*" * len(value)
    return "*" * (len(value) - int(show_last)) + value[-int(show_last) :]


def normalize_base_url(raw: str) -> str:
    """
    Normalize an OpenAI-compatible base_url.

    - Accepts forms like:
        https://api.openai.com/v1
        https://your-gateway.example.com/v1
        http://127.0.0.1:8000/v1
        http://127.0.0.1:8000
    - Returns a URL that ends with "/v1".
    """
    url = (raw or "").strip()
    if not url:
        return ""
    url = url.rstrip("/")
    if "/v1" not in url:
        url = url + "/v1"
    # If user provided ".../v1/...", keep it as-is (some gateways have nested paths).
    return url


def _http_json(
    method: str,
    url: str,
    *,
    payload: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout_s: float = 30.0,
) -> Tuple[int, dict]:
    data = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update({str(k): str(v) for k, v in headers.items() if v is not None})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() or b""
            try:
                return status, json.loads(body.decode("utf-8", errors="replace"))
            except Exception:
                raw = body.decode("utf-8", errors="replace").strip()
                if raw:
                    if len(raw) > 2000:
                        raw = raw[:2000] + "…"
                    return status, {"_raw": raw}
                return status, {}
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
            return int(e.code), json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            try:
                body = body if "body" in locals() else b""
                raw = (body or b"").decode("utf-8", errors="replace").strip()
                if raw:
                    if len(raw) > 2000:
                        raw = raw[:2000] + "…"
                    return int(getattr(e, "code", 500)), {"_raw": raw}
            except Exception:
                pass
            return int(getattr(e, "code", 500)), {}
    except Exception as e:
        # Network error, DNS, TLS, timeout etc.
        msg = (str(e) or "request failed").strip()
        if len(msg) > 500:
            msg = msg[:500] + "…"
        return 0, {"_error": msg}


def _is_transient_status(status: int) -> bool:
    s = int(status or 0)
    if s == 0:
        return True
    if s in (408, 429):
        return True
    return 500 <= s <= 599


def _looks_like_validation_required(data: dict) -> bool:
    """
    Some OpenAI-compatible gateways occasionally return HTTP 403 with a body that
    indicates "VALIDATION_REQUIRED" / "verify your account" (often transient).
    """
    if not isinstance(data, dict):
        return False
    raw = ""
    try:
        err = data.get("error", None)
        if isinstance(err, dict):
            raw = str(err.get("message", "") or "")
        if not raw:
            raw = str(data.get("_raw", "") or data.get("_error", "") or "")
    except Exception:
        raw = ""
    low = (raw or "").lower()
    return ("validation_required" in low) or ("verify your account" in low)


def _is_transient_response(status: int, data: dict) -> bool:
    s = int(status or 0)
    if _is_transient_status(s):
        return True
    if s == 403 and _looks_like_validation_required(data):
        return True
    return False


@dataclass(frozen=True)
class OpenAICompatConfig:
    api_key: str
    base_url: str
    model: str
    timeout_s: float = 60.0
    max_retries: int = 5
    base_retry_delay_s: float = 0.9
    max_retry_delay_s: float = 10.0

    @property
    def base_url_v1(self) -> str:
        return normalize_base_url(self.base_url)

    def auth_headers(self) -> dict:
        k = (self.api_key or "").strip()
        # Some gateways are protected by WAF rules that block non-browser User-Agents
        # (e.g. Cloudflare 1010). Use a conservative UA by default.
        base = {"User-Agent": "Mozilla/5.0"}
        if not k:
            return base
        base["Authorization"] = f"Bearer {k}"
        return base


class OpenAICompatClient:
    def __init__(self, cfg: OpenAICompatConfig):
        self.cfg = cfg

    def chat_completions(self, payload: dict, *, timeout_s: Optional[float] = None) -> Tuple[int, dict]:
        base = self.cfg.base_url_v1
        if not base:
            return 0, {}
        url = base.rstrip("/") + "/chat/completions"

        max_retries = max(0, min(int(self.cfg.max_retries or 0), 8))
        base_delay = float(self.cfg.base_retry_delay_s or 0.8)
        max_delay = float(self.cfg.max_retry_delay_s or 6.0)
        timeout = float(timeout_s if timeout_s is not None else (self.cfg.timeout_s or 60.0))

        last_status = 0
        last_data: dict = {}
        for attempt in range(max_retries + 1):
            status, data = _http_json(
                "POST",
                url,
                payload=payload,
                headers=self.cfg.auth_headers(),
                timeout_s=timeout,
            )
            last_status, last_data = int(status or 0), data if isinstance(data, dict) else {}
            if not _is_transient_response(last_status, last_data):
                return last_status, last_data

            # Retry with exponential backoff.
            if attempt < max_retries:
                delay = min(max_delay, max(0.0, base_delay * (2**attempt)))
                # Add jitter to avoid retry storms.
                try:
                    delay = delay * (0.85 + 0.3 * random.random())
                except Exception:
                    pass
                try:
                    time.sleep(delay)
                except Exception:
                    pass

        return last_status, last_data

    def chat(
        self,
        *,
        messages: list,
        temperature: float = 0.0,
        max_tokens: int = 900,
        response_format: Optional[dict] = None,
        timeout_s: float = 180.0,
    ) -> Tuple[int, dict]:
        model = (self.cfg.model or "").strip()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": float(temperature or 0.0),
            "max_tokens": int(max_tokens or 0),
        }
        if isinstance(response_format, dict) and response_format:
            payload["response_format"] = response_format

        status, data = self.chat_completions(payload, timeout_s=timeout_s)

        # JSON mode fallback (some providers reject response_format).
        if int(status or 0) in (400, 404, 405, 409, 415, 422) and "response_format" in payload:
            payload.pop("response_format", None)
            status2, data2 = self.chat_completions(payload, timeout_s=timeout_s)
            if int(status2 or 0) != 0:
                return status2, data2

        return status, data


def extract_first_content(resp: dict) -> str:
    if not isinstance(resp, dict):
        return ""
    try:
        choices = resp.get("choices", [])
        if isinstance(choices, list) and choices:
            c0 = choices[0] if isinstance(choices[0], dict) else {}
            msg = c0.get("message", {}) if isinstance(c0, dict) else {}
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content.strip()
                # Some gateways return "content" as a list of parts.
                if isinstance(content, list):
                    parts: list[str] = []
                    for it in content:
                        if isinstance(it, str):
                            parts.append(it)
                            continue
                        if isinstance(it, dict):
                            if isinstance(it.get("text", None), str):
                                parts.append(it.get("text", ""))
                                continue
                            if isinstance(it.get("content", None), str):
                                parts.append(it.get("content", ""))
                                continue
                    return "\n".join([p for p in parts if (p or "").strip()]).strip()
                if isinstance(content, dict) and isinstance(content.get("text", None), str):
                    return str(content.get("text", "") or "").strip()
            # Fallback: some providers use legacy "text" at the choice level.
            if isinstance(c0.get("text", None), str):
                return (c0.get("text", "") or "").strip()
    except Exception:
        return ""
    return ""


def extract_usage(resp: dict) -> Dict[str, int]:
    """
    Best-effort token usage extraction for OpenAI-compatible responses.

    Returns a dict with keys: prompt_tokens, completion_tokens, total_tokens.
    Missing fields are returned as 0.
    """
    if not isinstance(resp, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    usage = resp.get("usage", None)
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        pt = int(usage.get("prompt_tokens", 0) or 0)
    except Exception:
        pt = 0
    try:
        ct = int(usage.get("completion_tokens", 0) or 0)
    except Exception:
        ct = 0
    try:
        tt = int(usage.get("total_tokens", 0) or 0)
    except Exception:
        tt = pt + ct
    if tt <= 0:
        tt = pt + ct
    return {"prompt_tokens": max(0, pt), "completion_tokens": max(0, ct), "total_tokens": max(0, tt)}
