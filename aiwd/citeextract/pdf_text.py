from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def extract_pdf_pages(pdf_path: Path, *, max_pages: Optional[int] = None) -> List[str]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyMuPDF is required. Install with: pip install PyMuPDF") from exc

    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    try:
        pages: List[str] = []
        for page_index, page in enumerate(doc, start=1):
            if max_pages is not None and page_index > int(max_pages):
                break
            pages.append(_extract_page_text_blocks(page, fitz))
        return pages
    finally:
        doc.close()


def _extract_page_text_blocks(page, fitz_module) -> str:
    flags = 0
    try:
        flags |= int(getattr(fitz_module, "TEXT_DEHYPHENATE", 0))
    except Exception:
        flags = 0

    blocks = page.get_text("blocks", flags=flags) or []
    text_blocks = []
    for block in blocks:
        if not isinstance(block, (list, tuple)) or len(block) < 5:
            continue
        text = block[4]
        if not isinstance(text, str):
            continue
        block_type = None
        if len(block) >= 7 and isinstance(block[6], int):
            block_type = int(block[6])
        if block_type is not None and block_type != 0:
            continue
        text = text.strip()
        if not text:
            continue
        x0 = float(block[0]) if _is_number(block[0]) else 0.0
        y0 = float(block[1]) if _is_number(block[1]) else 0.0
        text_blocks.append((y0, x0, text))

    text_blocks.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    return "\n".join(t[2] for t in text_blocks)


def _is_number(x) -> bool:
    try:
        float(x)
        return True
    except Exception:
        return False

