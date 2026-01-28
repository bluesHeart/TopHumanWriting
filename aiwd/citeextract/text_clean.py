from __future__ import annotations

import re
from collections import Counter
from typing import List, Optional


_WS_RE = re.compile(r"[ \t]+")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DIGITS_RE = re.compile(r"\d+")


def remove_repeated_headers_footers(pages: List[str]) -> List[str]:
    if not pages or len(pages) < 3:
        return pages

    header_counts: Counter[str] = Counter()
    footer_counts: Counter[str] = Counter()

    for text in pages:
        lines = _non_empty_lines(text)
        if not lines:
            continue
        for line in lines[:2]:
            key = _norm_header_footer_line(line)
            if key:
                header_counts[key] += 1
        for line in lines[-2:]:
            key = _norm_header_footer_line(line)
            if key:
                footer_counts[key] += 1

    threshold = max(2, int(len(pages) * 0.6))
    drop = {k for k, v in header_counts.items() if v >= threshold} | {k for k, v in footer_counts.items() if v >= threshold}
    if not drop:
        return pages

    cleaned: List[str] = []
    for text in pages:
        kept_lines = []
        for line in text.splitlines():
            if _norm_header_footer_line(line) in drop:
                continue
            kept_lines.append(line)
        cleaned.append("\n".join(kept_lines))
    return cleaned


def page_has_references_heading(text: str) -> bool:
    if not text:
        return False

    for line in _non_empty_lines(text)[:30]:
        s = line.strip()
        if len(s) > 60:
            continue
        if re.fullmatch(r"(?i)references|bibliography|literature cited", s):
            return True
        if re.fullmatch(r"参考文献|引用文献|文献", s):
            return True
    return False


def find_references_heading_line_index(text: str) -> Optional[int]:
    if not text:
        return None
    lines = (text or "").splitlines()
    for idx, line in enumerate(lines[:300]):
        s = (line or "").strip()
        if not s or len(s) > 60:
            continue
        if re.fullmatch(r"(?i)references|bibliography|literature cited", s):
            return idx
        if re.fullmatch(r"参考文献|引用文献|文献", s):
            return idx
    return None


def normalize_for_sentence_split(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    text = text.replace("\u00ad", "")
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = _WS_RE.sub(" ", text)
    return text.strip()


def looks_like_reference_entry(sentence: str) -> bool:
    if not sentence:
        return False
    s = sentence.strip()
    if len(s) < 8:
        return False
    if re.match(r"^[A-Z][A-Za-z'’\-]+,\s*[A-Z](?:\.[A-Z])?\.", s):
        return True
    if re.match(r"^\[\d+\]\s*[A-Z][A-Za-z'’\-]+", s):
        return True
    return False


def _non_empty_lines(text: str) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if (ln or "").strip()]


def _norm_header_footer_line(line: str) -> str:
    if not line:
        return ""
    s = line.strip()
    if len(s) < 5 or len(s) > 160:
        return ""
    s = s.replace("\u00ad", "")
    s = _WS_RE.sub(" ", s)
    s = _DIGITS_RE.sub("", s)
    s = re.sub(r"[^A-Za-z\u4e00-\u9fff ]+", "", s)
    s = _WS_RE.sub(" ", s).strip().lower()
    if len(s) < 6:
        return ""
    return s
