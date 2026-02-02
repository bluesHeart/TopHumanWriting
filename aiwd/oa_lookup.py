# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


_HEADERS = {"User-Agent": "Mozilla/5.0 (TopHumanWriting)"}


def sanitize_filename(name: str, *, max_len: int = 140) -> str:
    s = (name or "").strip()
    if not s:
        return "paper"
    # Windows reserved characters
    s = re.sub(r'[<>:"/\\\\|?*]', " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return "paper"
    return s[: max(20, min(int(max_len or 140), 240))].rstrip(". ")


def _http_json_get(url: str, *, timeout_s: float = 20.0) -> Tuple[int, dict]:
    req = urllib.request.Request(url, headers=_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s or 20.0)) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() or b""
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
            return status, data if isinstance(data, dict) else {}
        except Exception:
            return status, {}
    except urllib.error.HTTPError as e:
        try:
            body = e.read() or b""
            data = json.loads(body.decode("utf-8", errors="replace"))
            return int(getattr(e, "code", 500) or 500), data if isinstance(data, dict) else {}
        except Exception:
            return int(getattr(e, "code", 500) or 500), {"_error": str(e)}
    except Exception as e:
        return 0, {"_error": str(e)}


def semantic_scholar_search(query: str, *, limit: int = 3, timeout_s: float = 20.0) -> List[dict]:
    q = (query or "").strip()
    if not q:
        return []
    lim = max(1, min(int(limit or 0), 10))
    params = {
        "query": q[:250],
        "limit": str(lim),
        "fields": "title,authors,year,externalIds,isOpenAccess,openAccessPdf,url",
    }
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)
    status, data = _http_json_get(url, timeout_s=timeout_s)
    if int(status or 0) != 200:
        return []
    items = data.get("data", [])
    if not isinstance(items, list):
        return []
    out: List[dict] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
    return out


def crossref_search(query: str, *, rows: int = 3, timeout_s: float = 20.0) -> List[dict]:
    q = (query or "").strip()
    if not q:
        return []
    n = max(1, min(int(rows or 0), 10))
    params = {"query": q[:250], "rows": str(n)}
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    status, data = _http_json_get(url, timeout_s=timeout_s)
    if int(status or 0) != 200:
        return []
    msg = data.get("message", {})
    if not isinstance(msg, dict):
        return []
    items = msg.get("items", [])
    if not isinstance(items, list):
        return []
    out: List[dict] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
    return out


def _year_close(a: Optional[int], b: Optional[int]) -> bool:
    try:
        if a is None or b is None:
            return False
        return abs(int(a) - int(b)) <= 1
    except Exception:
        return False


def pick_best_oa_candidate(
    *,
    semantic_items: List[dict],
    crossref_items: List[dict],
    target_year: str = "",
) -> dict:
    """
    Return a best-effort dict:
      {doi, title, year, is_open_access, oa_pdf_url, landing_url, source}
    """
    want_year: Optional[int] = None
    try:
        want_year = int(str(target_year or "").strip()[:4])
    except Exception:
        want_year = None

    best: Dict[str, Any] = {}

    # Prefer Semantic Scholar openAccessPdf.
    for it in semantic_items or []:
        try:
            year = it.get("year", None)
            try:
                year_i = int(year) if year is not None else None
            except Exception:
                year_i = None
            if want_year is not None and year_i is not None and not _year_close(want_year, year_i):
                continue

            ext = it.get("externalIds", {}) if isinstance(it.get("externalIds", {}), dict) else {}
            doi = (ext.get("DOI", "") or ext.get("doi", "") or "").strip()
            oa = bool(it.get("isOpenAccess", False))
            oa_pdf = it.get("openAccessPdf", {}) if isinstance(it.get("openAccessPdf", {}), dict) else {}
            oa_url = (oa_pdf.get("url", "") or "").strip()
            title = (it.get("title", "") or "").strip()
            landing = (it.get("url", "") or "").strip()

            if oa_url:
                best = {
                    "doi": doi,
                    "title": title,
                    "year": year_i or "",
                    "is_open_access": True,
                    "oa_pdf_url": oa_url,
                    "landing_url": landing,
                    "source": "semanticscholar",
                }
                return best

            if not best and doi:
                best = {
                    "doi": doi,
                    "title": title,
                    "year": year_i or "",
                    "is_open_access": oa,
                    "oa_pdf_url": "",
                    "landing_url": landing,
                    "source": "semanticscholar",
                }
        except Exception:
            continue

    # Fallback to Crossref DOI.
    for it in crossref_items or []:
        try:
            doi = (it.get("DOI", "") or "").strip()
            if not doi:
                continue

            year_i: Optional[int] = None
            try:
                parts = it.get("published", {}).get("date-parts", [[None]])[0]
                if isinstance(parts, list) and parts:
                    year_i = int(parts[0]) if parts[0] is not None else None
            except Exception:
                year_i = None
            if want_year is not None and year_i is not None and not _year_close(want_year, year_i):
                continue

            title0 = it.get("title", None)
            title = ""
            if isinstance(title0, list) and title0:
                title = str(title0[0] or "").strip()
            elif isinstance(title0, str):
                title = title0.strip()

            if not best:
                best = {
                    "doi": doi,
                    "title": title,
                    "year": year_i or "",
                    "is_open_access": False,
                    "oa_pdf_url": "",
                    "landing_url": f"https://doi.org/{doi}",
                    "source": "crossref",
                }
        except Exception:
            continue

    return best or {"doi": "", "title": "", "year": "", "is_open_access": False, "oa_pdf_url": "", "landing_url": "", "source": ""}


def download_pdf(
    url: str,
    dest_path: str,
    *,
    timeout_s: float = 60.0,
    max_bytes: int = 80 * 1024 * 1024,
) -> bool:
    """
    Download a PDF from a direct URL to dest_path.

    Safety:
    - Writes to a temp file then renames.
    - Hard cap on download size.
    """
    u = (url or "").strip()
    if not u:
        return False
    dest_path = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    tmp = f"{dest_path}.tmp_{secrets.token_hex(4)}"

    req = urllib.request.Request(u, headers=_HEADERS, method="GET")
    wrote = 0
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s or 60.0)) as resp:
            status = int(getattr(resp, "status", 200))
            if status != 200:
                return False
            ctype = str(resp.headers.get("content-type", "") or "").lower()
            with open(tmp, "wb") as f:
                first = resp.read(5)
                if not first:
                    return False
                wrote += len(first)
                f.write(first)
                # Best-effort PDF check:
                if b"%PDF" not in first and "pdf" not in ctype and not u.lower().endswith(".pdf"):
                    # Keep going a little; some servers omit content-type and leading bytes may vary.
                    pass
                while True:
                    chunk = resp.read(1024 * 128)
                    if not chunk:
                        break
                    wrote += len(chunk)
                    if wrote > int(max_bytes or 0):
                        return False
                    f.write(chunk)
        ok = wrote > 1024  # at least 1KB
        if ok:
            os.replace(tmp, dest_path)
        return ok
    except Exception:
        return False
    finally:
        if not ok:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
        # Rate limiting friendliness (avoid being flagged by some hosts)
        try:
            time.sleep(0.2)
        except Exception:
            pass

