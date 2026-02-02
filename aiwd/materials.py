# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ai_word_detector import (  # type: ignore
    LanguageDetector,
    _strip_heading_prefix,
    is_heading_like,
    normalize_soft_line_breaks_preserve_len,
    split_sentences_with_positions,
)

from aiwd.citeextract.pipeline import iter_citation_sentences_from_pages, load_pdf_pages
from aiwd.citeextract.references import iter_reference_entries_from_pages
from aiwd.citeextract.text_clean import find_references_heading_line_index, page_has_references_heading
from aiwd.openai_compat import OpenAICompatClient, extract_first_content
from aiwd.polish import extract_json


MATERIALS_VERSION = "0.1"


def _sha1_hex(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8", errors="ignore")).hexdigest()


def _file_sig(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"size": int(st.st_size), "mtime": int(st.st_mtime)}
    except Exception:
        return {"size": 0, "mtime": 0}


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _guess_lang(text: str, *, fallback: str = "en") -> str:
    try:
        lang = LanguageDetector.detect(text or "")
    except Exception:
        lang = fallback
    lang = (lang or fallback).strip().lower()
    return lang if lang in ("en", "zh", "mixed") else fallback


def _infer_heading_level(text: str, *, language: str) -> int:
    s = (text or "").strip()
    if not s:
        return 1

    m = re.match(r"^\s*(\d+(?:\.\d+){0,6})\b", s)
    if m:
        dots = m.group(1).count(".")
        return max(1, min(6, dots + 1))

    if language in ("zh", "mixed"):
        if re.match(r"^\s*[一二三四五六七八九十]+[、.．)]\s*", s):
            return 1
        if re.match(r"^\s*[（(][一二三四五六七八九十]+[)）]\s*", s):
            return 2
        if re.match(r"^\s*第[一二三四五六七八九十0-9]+[章节篇]\s*", s):
            return 1

    return 1


def _canonicalize_heading(text: str, *, language: str) -> str:
    s = (text or "").strip()
    if not s:
        return "other"

    base = _strip_heading_prefix(s).strip().rstrip(":：").strip()
    base = re.sub(r"[。！？；.!?]+$", "", base).strip()

    if language in ("zh", "mixed") and re.search(r"[\u4e00-\u9fff]", base):
        if base in ("摘要", "中文摘要", "英文摘要"):
            return "abstract"
        if base.startswith(("引言", "前言", "绪论")):
            return "introduction"
        if "文献综述" in base or "相关工作" in base:
            return "related_work"
        if any(x in base for x in ("数据", "样本", "变量")):
            return "data"
        if any(x in base for x in ("方法", "研究设计", "研究方法", "模型")):
            return "methods"
        if any(x in base for x in ("结果", "讨论", "实证结果")):
            return "results"
        if base.startswith(("结论", "总结")):
            return "conclusion"
        if base.startswith(("参考文献", "引用文献", "文献")):
            return "references"
        if base.startswith("附录"):
            return "appendix"
        if base.startswith("致谢"):
            return "acknowledgements"
        return "other"

    base_l = base.lower()
    if base_l == "abstract" or base_l.startswith("abstract "):
        return "abstract"
    if base_l.startswith("intro") or "introduction" in base_l:
        return "introduction"
    if "related work" in base_l or "literature review" in base_l or "literature" == base_l or base_l.startswith("literature "):
        return "related_work"
    if re.search(r"\bdata\b", base_l) or "sample" in base_l:
        return "data"
    if any(k in base_l for k in ("method", "methodology", "empirical strategy", "identification", "model", "specification")):
        return "methods"
    if any(k in base_l for k in ("result", "discussion", "empirical", "findings")):
        return "results"
    if "robust" in base_l or "additional tests" in base_l:
        return "robustness"
    if "conclu" in base_l or base_l.startswith("summary"):
        return "conclusion"
    if base_l.startswith("reference") or base_l.startswith("bibliograph") or base_l.startswith("literature cited"):
        return "references"
    if base_l.startswith("appendix"):
        return "appendix"
    if base_l.startswith("acknowledg"):
        return "acknowledgements"

    if re.match(r"(?i)^(table|figure|fig\\.|eq\\.|equation)\\b", s.strip()):
        return "figure_table"

    return "other"


def _cut_pages_before_references(pages: List[str]) -> List[str]:
    if not pages:
        return pages
    for i, text in enumerate(pages):
        if page_has_references_heading(text):
            kept = pages[:i]
            idx = find_references_heading_line_index(text)
            if idx is not None:
                try:
                    lines = (text or "").splitlines()
                    head = "\n".join(lines[:idx]).strip()
                except Exception:
                    head = ""
                if head:
                    kept.append(head)
            return kept
    return pages


def _iter_blocks_from_page(text: str, *, page: int, language: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    buf: List[str] = []

    def flush_para():
        nonlocal buf
        if not buf:
            return
        para = _norm_ws(" ".join(buf))
        buf = []
        if not para:
            return
        out.append({"type": "paragraph", "page": int(page), "text": para, "lang": language})

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for ln in lines:
        s = (ln or "").strip()
        if not s:
            flush_para()
            continue

        # Keep headings compact to avoid swallowing entire paragraphs.
        if len(s) <= 140 and is_heading_like(s, language):
            flush_para()
            out.append(
                {
                    "type": "heading",
                    "page": int(page),
                    "text": _norm_ws(s),
                    "lang": language,
                }
            )
            continue

        buf.append(s)

    flush_para()
    return out


def _split_sentences(text: str, *, language: str) -> List[Dict[str, Any]]:
    t = (text or "").strip()
    if not t:
        return []
    lang = language if language in ("en", "zh", "mixed") else _guess_lang(t)
    try:
        sents = split_sentences_with_positions(t, lang)
    except Exception:
        sents = []
    out = []
    sid = 0
    for sent, s, e in sents:
        st = _norm_ws(sent)
        if not st:
            continue
        # Avoid treating headings as sentences.
        if len(st) <= 140 and is_heading_like(st, lang):
            continue
        out.append({"id": sid, "text": st, "start": int(s), "end": int(e), "lang": _guess_lang(st, fallback=lang if lang != "mixed" else "en")})
        sid += 1
    return out


def build_material_doc(
    *,
    pdf_path: str,
    pdf_root: str,
    llm: Optional[OpenAICompatClient] = None,
    llm_timeout_s: float = 180.0,
) -> Dict[str, Any]:
    pdf_path = os.path.abspath(pdf_path)
    pdf_root = os.path.abspath(pdf_root)
    rel = os.path.relpath(pdf_path, pdf_root).replace("\\", "/")
    doc_id = _sha1_hex(rel)[:16]

    pages_all = load_pdf_pages(Path(pdf_path))
    pages_prose = _cut_pages_before_references(pages_all)
    head_text = "\n".join((pages_prose or pages_all)[: min(2, len(pages_prose or pages_all))])
    primary_lang = _guess_lang(head_text)

    blocks: List[Dict[str, Any]] = []
    for i, text in enumerate(pages_prose, start=1):
        lang = primary_lang
        if primary_lang == "mixed":
            lang = _guess_lang(text, fallback="en")
        blocks.extend(_iter_blocks_from_page(text, page=i, language=lang))

    # Build sections + paragraphs.
    sections: List[Dict[str, Any]] = []
    paragraphs: List[Dict[str, Any]] = []
    section_stack: List[Tuple[int, str]] = []  # (level, section_id)
    sec_idx = 0
    para_idx = 0

    def current_section_id() -> str:
        return section_stack[-1][1] if section_stack else ""

    headings: List[Dict[str, Any]] = []
    for b in blocks:
        if b.get("type") == "heading":
            t = str(b.get("text", "") or "")
            lang = str(b.get("lang", "") or primary_lang)
            lvl = _infer_heading_level(t, language=lang)
            canon = _canonicalize_heading(t, language=lang)
            sec_id = f"S{sec_idx}"
            sec_idx += 1

            # pop until a parent level is found
            while section_stack and section_stack[-1][0] >= lvl:
                section_stack.pop()
            parent = section_stack[-1][1] if section_stack else ""
            sections.append(
                {
                    "id": sec_id,
                    "parent": parent,
                    "level": int(lvl),
                    "page": int(b.get("page", 0) or 0),
                    "title": t,
                    "canonical": canon,
                    "lang": lang,
                }
            )
            headings.append({"page": int(b.get("page", 0) or 0), "level": int(lvl), "text": t, "canonical": canon})
            section_stack.append((lvl, sec_id))
            continue

        if b.get("type") != "paragraph":
            continue
        t = str(b.get("text", "") or "")
        if not t:
            continue
        lang = str(b.get("lang", "") or primary_lang)
        pid = f"P{para_idx}"
        para_idx += 1
        paragraphs.append(
            {
                "id": pid,
                "page": int(b.get("page", 0) or 0),
                "section": current_section_id(),
                "text": t,
                "lang": lang,
                "sentences": _split_sentences(t, language=lang),
            }
        )

    # Citation sentences (stop at references).
    citations = []
    try:
        for rec in iter_citation_sentences_from_pages(pages_all, pdf_label=os.path.basename(pdf_path), stop_at_references=True):
            citations.append(rec.to_dict())
    except Exception:
        citations = []

    # Reference entries (from full pages).
    references = []
    try:
        for r in iter_reference_entries_from_pages(pages_all, pdf_label=os.path.basename(pdf_path)):
            try:
                references.append(
                    {
                        "authors": str(getattr(r, "authors", "") or ""),
                        "year": str(getattr(r, "year", "") or ""),
                        "title": str(getattr(r, "title", "") or ""),
                        "reference": str(getattr(r, "reference", "") or "")[:2400],
                    }
                )
            except Exception:
                continue
    except Exception:
        references = []

    out: Dict[str, Any] = {
        "meta": {
            "doc_id": doc_id,
            "pdf_rel": rel,
            "pages_total": int(len(pages_all)),
            "pages_prose": int(len(pages_prose)),
            "language": primary_lang,
            "built_at": int(time.time()),
            "version": MATERIALS_VERSION,
        },
        "headings": headings,
        "sections": sections,
        "paragraphs": paragraphs,
        "citations": citations,
        "references": references,
        "llm": {"used": False, "model": "", "notes": []},
    }

    # Optional LLM enhancement: refine heading canonicalization when ambiguous.
    if llm is not None and headings:
        try:
            amb = [h for h in headings if str(h.get("canonical", "")) == "other"]
            if amb:
                prompt = _build_heading_label_prompt(headings=headings, language=primary_lang)
                status, resp = llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=900,
                    response_format={"type": "json_object"},
                    timeout_s=float(llm_timeout_s),
                )
                txt = extract_first_content(resp)
                obj = extract_json(txt) or {}
                labels = obj.get("labels", [])
                if isinstance(labels, list):
                    idx2canon = {}
                    for it in labels:
                        if not isinstance(it, dict):
                            continue
                        i = it.get("i", None)
                        c = str(it.get("canonical", "") or "").strip()
                        if isinstance(i, int) and c:
                            idx2canon[int(i)] = c
                    # apply
                    for i, h in enumerate(headings):
                        if i in idx2canon:
                            h["canonical"] = str(idx2canon[i])
                    # also patch sections list
                    for sec in sections:
                        for i, h in enumerate(headings):
                            if int(sec.get("page", 0) or 0) == int(h.get("page", 0) or 0) and str(sec.get("title", "")) == str(h.get("text", "")):
                                sec["canonical"] = str(h.get("canonical", "") or sec.get("canonical", "other"))
                out["llm"] = {"used": True, "model": str(getattr(llm, "cfg", None).model if getattr(llm, "cfg", None) else ""), "notes": []}
        except Exception as e:
            out["llm"] = {"used": False, "model": "", "notes": [str(e)[:300]]}

    return out


def _build_heading_label_prompt(*, headings: List[Dict[str, Any]], language: str) -> str:
    allowed = [
        "abstract",
        "introduction",
        "related_work",
        "data",
        "methods",
        "results",
        "robustness",
        "conclusion",
        "appendix",
        "references",
        "acknowledgements",
        "figure_table",
        "other",
    ]
    lines = []
    lines.append("You label section headings into a small canonical set for academic papers.")
    lines.append("OUTPUT MUST BE JSON.")
    lines.append("RULES:")
    lines.append("- Only use canonical values from: " + ", ".join(allowed))
    lines.append("- Do not invent new headings. Only label the given ones.")
    lines.append("- Keep 'figure_table' for Table/Figure/Eq headings.")
    lines.append("")
    lines.append(f"LANGUAGE_HINT: {language}")
    lines.append("")
    lines.append("HEADINGS:")
    for i, h in enumerate(headings):
        t = str(h.get("text", "") or "")
        page = int(h.get("page", 0) or 0)
        lines.append(f"{i}. (p{page}) {t}")
    lines.append("")
    lines.append("OUTPUT_SCHEMA:")
    lines.append(
        json.dumps(
            {
                "labels": [
                    {
                        "i": 0,
                        "canonical": "introduction",
                    }
                ]
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(lines).strip()


@dataclass
class MaterialsBuildStats:
    pdf_count: int = 0
    doc_count: int = 0
    citation_sentence_count: int = 0
    reference_count: int = 0


class MaterialsError(RuntimeError):
    pass


class MaterialsIndexer:
    """
    Build & store a structured exemplar material library (JSON).

    Layout:
      <data_dir>/materials/<library_slug>/
        manifest.json
        docs/<doc_id>.json
    """

    def __init__(self, *, data_dir: str, library_name: str):
        self.data_dir = data_dir
        self.library_name = (library_name or "").strip()
        if not self.library_name:
            raise MaterialsError("library not selected")
        self.root_dir = os.path.join(self.data_dir, "materials", self.library_name)
        self.docs_dir = os.path.join(self.root_dir, "docs")
        self.manifest_path = os.path.join(self.root_dir, "manifest.json")

    def index_ready(self) -> bool:
        return os.path.exists(self.manifest_path) and os.path.exists(self.docs_dir)

    def load_manifest(self) -> dict:
        try:
            if os.path.exists(self.manifest_path):
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
        return {}

    def build(
        self,
        *,
        pdf_root: str,
        llm: Optional[OpenAICompatClient] = None,
        use_llm: bool = False,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        max_pdfs: Optional[int] = None,
    ) -> MaterialsBuildStats:
        pdf_root = os.path.abspath(pdf_root)
        if not os.path.exists(pdf_root):
            raise MaterialsError("pdf_root not found")

        os.makedirs(self.docs_dir, exist_ok=True)

        pdf_files = sorted(list(Path(pdf_root).rglob("*.pdf")), key=lambda p: str(p).lower())
        if max_pdfs is not None:
            try:
                pdf_files = pdf_files[: max(1, int(max_pdfs))]
            except Exception:
                pass
        total = len(pdf_files)

        stats = MaterialsBuildStats(pdf_count=total)
        docs_index: List[Dict[str, Any]] = []
        outline_seqs: Dict[str, int] = {}
        outline_examples: Dict[str, Dict[str, Any]] = {}
        files: Dict[str, dict] = {}

        old_manifest = self.load_manifest()
        old_files = old_manifest.get("files", {}) if isinstance(old_manifest, dict) else {}
        if not isinstance(old_files, dict):
            old_files = {}

        def report(stage: str, done: int, detail: str):
            if progress_cb:
                try:
                    progress_cb(stage, int(done), int(total), str(detail or ""))
                except Exception:
                    pass

        report("materials_scan", 0, "扫描 PDF…")

        for i, pdf_path in enumerate(pdf_files, start=1):
            if cancel_cb and cancel_cb():
                break
            try:
                rel = os.path.relpath(str(pdf_path), pdf_root).replace("\\", "/")
            except Exception:
                rel = pdf_path.name
            files[rel] = _file_sig(str(pdf_path))

            doc_id = _sha1_hex(rel)[:16]
            out_path = os.path.join(self.docs_dir, f"{doc_id}.json")
            report("materials_doc", i - 1, rel)

            doc: Dict[str, Any] = {}
            cached_ok = False
            try:
                old_sig = old_files.get(rel, None)
                if isinstance(old_sig, dict) and old_sig == files[rel] and os.path.exists(out_path):
                    with open(out_path, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                    cached_ok = isinstance(doc, dict)
            except Exception:
                cached_ok = False

            if not cached_ok:
                doc = build_material_doc(
                    pdf_path=str(pdf_path),
                    pdf_root=pdf_root,
                    llm=llm if bool(use_llm) else None,
                )
                # Force stable ids based on rel path to keep cache usable.
                try:
                    meta = doc.get("meta", {})
                    if isinstance(meta, dict):
                        meta["doc_id"] = doc_id
                        meta["pdf_rel"] = rel
                except Exception:
                    pass

                tmp = out_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                os.replace(tmp, out_path)

            docs_index.append(
                {
                    "doc_id": doc_id,
                    "pdf_rel": rel,
                    "path": f"docs/{doc_id}.json",
                    "pages_total": int(((doc.get("meta") or {}).get("pages_total")) or 0),
                    "language": str(((doc.get("meta") or {}).get("language")) or ""),
                    "citation_sentence_count": int(len(doc.get("citations", []) or [])),
                    "reference_count": int(len(doc.get("references", []) or [])),
                }
            )

            stats.doc_count += 1
            stats.citation_sentence_count += int(len(doc.get("citations", []) or []))
            stats.reference_count += int(len(doc.get("references", []) or []))

            # Outline stats (canonical headings, excluding noisy ones).
            canon = []
            for h in doc.get("headings", []) or []:
                c = str(h.get("canonical", "") or "").strip()
                if not c or c in ("other", "figure_table", "references", "appendix", "acknowledgements"):
                    continue
                canon.append(c)
            if canon:
                key = " > ".join(canon[:18])
                outline_seqs[key] = outline_seqs.get(key, 0) + 1
                if key not in outline_examples:
                    outline_examples[key] = {"pdf_rel": rel, "canon": canon[:18]}

            report("materials_doc", i, rel)

        # Top outlines
        top_outlines = sorted(outline_seqs.items(), key=lambda kv: (-int(kv[1]), kv[0]))[:8]
        outline_templates = []
        for seq, cnt in top_outlines:
            ex = outline_examples.get(seq, {})
            outline_templates.append({"seq": seq, "count": int(cnt), "example": ex})

        manifest = {
            "version": MATERIALS_VERSION,
            "built_at": int(time.time()),
            "library": self.library_name,
            "pdf_root": pdf_root.replace("\\", "/"),
            "doc_count": int(stats.doc_count),
            "stats": {
                "citation_sentence_count": int(stats.citation_sentence_count),
                "reference_count": int(stats.reference_count),
            },
            "outlines": outline_templates,
            "docs": docs_index,
            "files": files,
        }

        tmpm = self.manifest_path + ".tmp"
        with open(tmpm, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmpm, self.manifest_path)

        report("materials_done", int(stats.doc_count), "完成")
        return stats
