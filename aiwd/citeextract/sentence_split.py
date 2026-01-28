from __future__ import annotations

import re
from typing import List


_PARA_RE = re.compile(r"\n{2,}")
_WS_RE = re.compile(r"\s+")

_ABBREV_TAILS = (
    "e.g.",
    "i.e.",
    "et al.",
)

_ABBREV_WORDS = {
    "fig",
    "eq",
    "sec",
    "no",
    "dr",
    "mr",
    "ms",
    "prof",
    "jr",
    "sr",
    "st",
    "vs",
    "etc",
}


def split_sentences(text: str) -> List[str]:
    if not text:
        return []

    text = text.strip()
    if not text:
        return []

    out: List[str] = []
    for para in _PARA_RE.split(text):
        para = para.strip()
        if not para:
            continue
        out.extend(_split_para(para))
    return out


def _split_para(text: str) -> List[str]:
    n = len(text)
    start = 0
    i = 0
    out: List[str] = []

    while i < n:
        ch = text[i]
        if ch in ".?!":
            j = i + 1
            while j < n and text[j] in ".?!":
                j += 1

            if _is_sentence_end(text, i, j):
                end = j
                while end < n and text[end] in ")]}\"'":
                    end += 1
                sent = text[start:end].strip()
                if sent:
                    out.append(_cleanup_sentence(sent))
                start = end
                i = end
                continue

            i = j
            continue

        i += 1

    tail = text[start:].strip()
    if tail:
        out.append(_cleanup_sentence(tail))
    return out


def _is_sentence_end(text: str, period_i: int, after_punct_i: int) -> bool:
    n = len(text)
    window = text[max(0, period_i - 12) : min(n, after_punct_i)].lower()
    if any(window.endswith(t) for t in _ABBREV_TAILS):
        return False

    if text[period_i] == ".":
        if period_i > 0 and after_punct_i < n and text[period_i - 1].isdigit() and text[after_punct_i].isdigit():
            return False

        m = re.search(r"([A-Za-z]{1,6})\.$", window)
        if m and m.group(1).lower() in _ABBREV_WORDS:
            return False

        if re.search(r"(?:\b[A-Z]\.){2,}$", text[: after_punct_i]):
            return False

    k = after_punct_i
    while k < n and text[k].isspace():
        k += 1
    if k >= n:
        return True

    nxt = text[k]
    if nxt.islower():
        return False

    return True


def _cleanup_sentence(s: str) -> str:
    s = s.replace("\u00ad", "")
    s = _WS_RE.sub(" ", s).strip()
    return s

