from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .citation import YEAR_RE
from .text_clean import find_references_heading_line_index, page_has_references_heading


_WS_RE = re.compile(r"\s+")
_NUM_PREFIX_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+")

_REF_START_NUMERIC_RE = re.compile(r"^\s*(?:\[\d+\]|\d+\.)\s+")
_REF_START_AUTHOR_RE = re.compile(r"^[A-Z][A-Za-z'’\-]+,")
_REF_START_AUTHOR_NO_COMMA_RE = re.compile(r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){0,2}\s*\(")


@dataclass(frozen=True)
class ReferenceEntry:
    pdf: str
    page: int
    index: int
    reference: str
    authors: str
    year: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


def iter_reference_entries_from_pages(pages: List[str], *, pdf_label: str) -> Iterator[ReferenceEntry]:
    start_page = _find_references_start_page(pages)
    if start_page is None:
        return

    entry_index = 0
    cur_parts: List[str] = []
    cur_start_page: Optional[int] = None

    for page_i in range(start_page, len(pages)):
        for line in _iter_reference_lines(pages[page_i], is_first_page=(page_i == start_page)):
            if _is_new_reference_line(line):
                if cur_parts:
                    entry_index += 1
                    ref_text = _join_ref_parts(cur_parts)
                    authors, year = _parse_authors_year(ref_text)
                    if year:
                        yield ReferenceEntry(
                            pdf=pdf_label,
                            page=int(cur_start_page or (page_i + 1)),
                            index=entry_index,
                            reference=ref_text,
                            authors=authors,
                            year=year,
                        )
                cur_parts = [line]
                cur_start_page = page_i + 1
            else:
                if not cur_parts:
                    cur_parts = [line]
                    cur_start_page = page_i + 1
                else:
                    cur_parts.append(line)

    if cur_parts:
        entry_index += 1
        ref_text = _join_ref_parts(cur_parts)
        authors, year = _parse_authors_year(ref_text)
        if year:
            yield ReferenceEntry(
                pdf=pdf_label,
                page=int(cur_start_page or (start_page + 1)),
                index=entry_index,
                reference=ref_text,
                authors=authors,
                year=year,
            )


def _find_references_start_page(pages: List[str]) -> Optional[int]:
    for i, text in enumerate(pages):
        if page_has_references_heading(text):
            return i
    return None


def _iter_reference_lines(page_text: str, *, is_first_page: bool) -> Iterator[str]:
    raw_lines = (page_text or "").splitlines()

    if is_first_page:
        idx = find_references_heading_line_index(page_text)
        if idx is not None and 0 <= idx < len(raw_lines):
            raw_lines = raw_lines[idx + 1 :]

    for line in raw_lines:
        s = (line or "").strip()
        if not s:
            continue
        if len(s) <= 60 and page_has_references_heading(s):
            continue
        if re.fullmatch(r"\d{1,4}", s):
            continue
        s = _WS_RE.sub(" ", s)
        yield s


def _is_new_reference_line(line: str) -> bool:
    if not line:
        return False
    if _REF_START_NUMERIC_RE.match(line):
        return True
    if _REF_START_AUTHOR_RE.match(line) and YEAR_RE.search(line[:220]):
        return True
    if _REF_START_AUTHOR_NO_COMMA_RE.match(line) and YEAR_RE.search(line[:220]):
        return True
    return False


def _join_ref_parts(parts: List[str]) -> str:
    s = " ".join(p.strip() for p in parts if (p or "").strip())
    s = _WS_RE.sub(" ", s).strip()
    return s


def _parse_authors_year(reference_text: str) -> Tuple[str, str]:
    if not reference_text:
        return ("", "")

    s = _NUM_PREFIX_RE.sub("", reference_text).strip()
    m = YEAR_RE.search(s)
    if not m:
        return ("", "")
    year = m.group(0)

    prefix = s[: m.start()]
    prefix = prefix.strip().rstrip("([,.;:").strip()
    authors = prefix
    authors = _WS_RE.sub(" ", authors).strip()
    return (authors, year)

