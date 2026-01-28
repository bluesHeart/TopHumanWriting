from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


YEAR_PATTERN = r"(?:18|19|20)\d{2}[a-zA-Z]?"
YEAR_RE = re.compile(rf"\b{YEAR_PATTERN}\b")

_PAREN_RE = re.compile(r"\(([^()]{0,300})\)")

_AUTH_TOKEN = r"[A-Z][A-Za-z'’\-]+"
_AUTHORS_PATTERN = (
    rf"{_AUTH_TOKEN}"
    rf"(?:,\s*{_AUTH_TOKEN})*"
    rf"(?:,\s*)?"
    rf"(?:\s+(?:and|&)\s+{_AUTH_TOKEN})?"
    rf"(?:\s+et\s+al\.)?"
)
_NARRATIVE_RE = re.compile(
    rf"\b(?P<authors>{_AUTHORS_PATTERN})\s*\(\s*(?P<years>{YEAR_PATTERN}(?:\s*[,;]\s*{YEAR_PATTERN})*)\s*\)"
)

_PAREN_EXCLUDE_RE = re.compile(r"(?i)^\s*(?:fig|figure|table|eq|equation|appendix|section|sec|chap|chapter)\b")
_NARRATIVE_EXCLUDE_HEAD = {"fig", "figure", "table", "eq", "equation", "appendix", "section", "sec", "chap", "chapter", "panel"}


@dataclass(frozen=True)
class Citation:
    kind: str  # "parenthetical" | "narrative"
    authors: str
    year: str
    raw: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


def find_citations(sentence: str) -> List[Citation]:
    if not sentence:
        return []

    citations: List[Citation] = []

    for m in _NARRATIVE_RE.finditer(sentence):
        authors = (m.group("authors") or "").strip()
        head = authors.split()[0].strip(",").lower() if authors else ""
        if head in _NARRATIVE_EXCLUDE_HEAD:
            continue
        years = _extract_years(m.group("years") or "")
        for year in years:
            citations.append(Citation(kind="narrative", authors=authors, year=year, raw=m.group(0)))

    for m in _PAREN_RE.finditer(sentence):
        inner = (m.group(1) or "").strip()
        if not inner or not YEAR_RE.search(inner):
            continue
        if _PAREN_EXCLUDE_RE.search(inner):
            continue
        if "," not in inner:
            continue
        citations.extend(_parse_parenthetical(inner, raw=m.group(0)))

    return citations


def _parse_parenthetical(inner: str, *, raw: str) -> List[Citation]:
    parts = re.split(r"\s*[;；]\s*", inner)
    out: List[Citation] = []
    last_authors: Optional[str] = None
    for part in parts:
        part = _strip_prefixes(part.strip())
        if not part:
            continue

        m = re.match(r"^(?P<authors>[^,]{2,120})\s*,\s*(?P<rest>.*)$", part)
        if m:
            authors = (m.group("authors") or "").strip()
            years = _extract_years(m.group("rest") or "")
            if authors and years:
                last_authors = authors
                for year in years:
                    out.append(Citation(kind="parenthetical", authors=authors, year=year, raw=raw))
            continue

        years = _extract_years(part)
        if years and last_authors:
            for year in years:
                out.append(Citation(kind="parenthetical", authors=last_authors, year=year, raw=raw))

    return out


def _strip_prefixes(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)^\s*(?:see|e\.g\.|i\.e\.|cf\.|for example|e\.g\.,|i\.e\.,)\s*", "", s)
    return s.strip()


def _extract_years(s: str) -> List[str]:
    if not s:
        return []

    years: List[str] = []
    for token in YEAR_RE.findall(s):
        years.append(token)

    for m in re.finditer(
        rf"\b((?:18|19|20)\d{{2}})([a-zA-Z])\b\s*(?:,|;)\s*([a-zA-Z](?:\s*(?:,|;)\s*[a-zA-Z])*)",
        s,
    ):
        base = m.group(1)
        letters = [m.group(2)] + re.findall(r"[a-zA-Z]", m.group(3) or "")
        for letter in letters:
            years.append(base + letter)

    seen = set()
    out: List[str] = []
    for y in years:
        y = (y or "").strip()
        if not y or y in seen:
            continue
        seen.add(y)
        out.append(y)
    return out

