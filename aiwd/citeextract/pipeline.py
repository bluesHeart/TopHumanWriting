from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from .citation import find_citations
from .pdf_text import extract_pdf_pages
from .references import ReferenceEntry, iter_reference_entries_from_pages
from .sentence_split import split_sentences
from .text_clean import (
    find_references_heading_line_index,
    looks_like_reference_entry,
    normalize_for_sentence_split,
    page_has_references_heading,
    remove_repeated_headers_footers,
)


@dataclass(frozen=True)
class CitationSentenceRecord:
    pdf: str
    page: int
    sentence: str
    citations: List[Dict[str, str]]

    def to_dict(self) -> Dict[str, object]:
        return {
            "pdf": self.pdf,
            "page": int(self.page),
            "sentence": self.sentence,
            "citations": self.citations,
        }


def load_pdf_pages(pdf_path: Path, *, max_pages: Optional[int] = None) -> List[str]:
    pages = extract_pdf_pages(Path(pdf_path), max_pages=max_pages)
    return remove_repeated_headers_footers(pages)


def iter_citation_sentences_from_pages(
    pages: List[str],
    *,
    pdf_label: str,
    stop_at_references: bool = True,
) -> Iterator[CitationSentenceRecord]:
    if stop_at_references:
        # Keep text before the References heading on the same page (common when references start mid-page).
        cut_page = _find_references_start(pages)
        if cut_page is not None:
            kept = pages[:cut_page]
            try:
                idx = find_references_heading_line_index(pages[cut_page])
            except Exception:
                idx = None
            if idx is not None:
                try:
                    lines = (pages[cut_page] or "").splitlines()
                    head = "\n".join(lines[: idx]).strip()
                    if head:
                        kept.append(head)
                except Exception:
                    pass
            pages = kept

    for page_num, page_text in enumerate(pages, start=1):
        clean = normalize_for_sentence_split(page_text)
        for sent in split_sentences(clean):
            if looks_like_reference_entry(sent):
                continue
            cits = find_citations(sent)
            if not cits:
                continue
            yield CitationSentenceRecord(
                pdf=pdf_label,
                page=page_num,
                sentence=sent,
                citations=[c.to_dict() for c in cits],
            )


def iter_citation_sentences(
    pdf_path: Path,
    *,
    pdf_label: Optional[str] = None,
    max_pages: Optional[int] = None,
    stop_at_references: bool = True,
) -> Iterator[CitationSentenceRecord]:
    label = pdf_label if pdf_label is not None else str(pdf_path)
    pages = load_pdf_pages(Path(pdf_path), max_pages=max_pages)
    yield from iter_citation_sentences_from_pages(pages, pdf_label=label, stop_at_references=stop_at_references)


def iter_reference_entries(
    pdf_path: Path,
    *,
    pdf_label: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> Iterator[ReferenceEntry]:
    label = pdf_label if pdf_label is not None else str(pdf_path)
    pages = load_pdf_pages(Path(pdf_path), max_pages=max_pages)
    yield from iter_reference_entries_from_pages(pages, pdf_label=label)


def _find_references_start(pages: List[str]) -> Optional[int]:
    for i, text in enumerate(pages):
        if page_has_references_heading(text):
            return i
    return None
