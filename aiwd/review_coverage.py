# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _safe_filename(name: str, *, fallback: str = "default") -> str:
    s = (name or "").strip()
    if not s:
        return fallback
    s = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", s).strip("._ ")
    if not s:
        return fallback
    if len(s) > 120:
        h = hashlib.sha1(s.encode("utf-8", errors="ignore")).hexdigest()[:10]
        s = s[:100].rstrip("._ ") + "_" + h
    return s


def stable_text_key(*, prefix: str, page: int = 0, text: str = "", extra: str = "") -> str:
    p = int(page or 0)
    t = re.sub(r"\s+", " ", (text or "")).strip()
    base = f"{p}|{t[:1200]}|{extra[:200]}".strip("|")
    h = hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()[:16]
    pre = (prefix or "k").strip()[:6]
    return f"{pre}_{h}"


@dataclass
class ReviewCoverageStore:
    path: str
    data: Dict[str, Any]

    @classmethod
    def load_or_create(cls, *, dir_path: str, series_id: str) -> "ReviewCoverageStore":
        os.makedirs(dir_path, exist_ok=True)
        fn = _safe_filename(series_id, fallback="series")
        path = os.path.join(dir_path, f"{fn}.json")
        data: Dict[str, Any] = {"version": 1, "series_id": str(series_id or ""), "updated_at": 0, "contexts": {}, "categories": {}}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    data.update(obj)
            except Exception:
                pass
        return cls(path=path, data=data)

    def _cat(self, category: str) -> Dict[str, Any]:
        cats = self.data.setdefault("categories", {})
        cat = cats.get(category, None)
        if not isinstance(cat, dict):
            cat = {"items": {}}
            cats[category] = cat
        items = cat.get("items", None)
        if not isinstance(items, dict):
            cat["items"] = {}
        return cat

    def seen_count(self, category: str, key: str) -> int:
        key = str(key or "").strip()
        if not key:
            return 0
        items = self._cat(category).get("items", {})
        ent = items.get(key, None) if isinstance(items, dict) else None
        if not isinstance(ent, dict):
            return 0
        try:
            return int(ent.get("count", 0) or 0)
        except Exception:
            return 0

    def page_seen_count(self, category: str, page: int) -> int:
        try:
            p = int(page or 0)
        except Exception:
            p = 0
        if p <= 0:
            return 0
        items = self._cat(category).get("items", {})
        if not isinstance(items, dict) or not items:
            return 0
        n = 0
        for ent in items.values():
            if not isinstance(ent, dict):
                continue
            try:
                if int(ent.get("page", 0) or 0) == p:
                    n += 1
            except Exception:
                continue
        return int(n)

    def mark_seen(self, category: str, key: str, *, page: int = 0, meta: Optional[dict] = None):
        key = str(key or "").strip()
        if not key:
            return
        cat = self._cat(category)
        items = cat.get("items", {})
        if not isinstance(items, dict):
            items = {}
            cat["items"] = items
        ent = items.get(key, None)
        if not isinstance(ent, dict):
            ent = {"count": 0, "page": int(page or 0), "first_seen_at": int(time.time()), "last_seen_at": 0}
            items[key] = ent
        try:
            ent["count"] = int(ent.get("count", 0) or 0) + 1
        except Exception:
            ent["count"] = 1
        if page:
            try:
                ent["page"] = int(page or 0)
            except Exception:
                pass
        ent["last_seen_at"] = int(time.time())
        if isinstance(meta, dict) and meta:
            try:
                ent_meta = ent.get("meta", None)
                if not isinstance(ent_meta, dict):
                    ent_meta = {}
                    ent["meta"] = ent_meta
                for k, v in meta.items():
                    if v is None:
                        continue
                    ent_meta[str(k)] = v
            except Exception:
                pass

    def get_context(self, key: str) -> Any:
        ctx = self.data.get("contexts", {})
        if not isinstance(ctx, dict):
            return None
        return ctx.get(str(key), None)

    def set_context(self, key: str, value: Any):
        ctx = self.data.setdefault("contexts", {})
        if not isinstance(ctx, dict):
            self.data["contexts"] = {}
            ctx = self.data["contexts"]
        ctx[str(key)] = value

    def clear_category(self, category: str):
        cats = self.data.setdefault("categories", {})
        if not isinstance(cats, dict):
            self.data["categories"] = {}
            cats = self.data["categories"]
        cats[category] = {"items": {}}

    def save(self):
        try:
            self.data["updated_at"] = int(time.time())
        except Exception:
            pass
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

