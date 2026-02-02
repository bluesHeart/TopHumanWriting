# -*- coding: utf-8 -*-

from __future__ import annotations

from collections import Counter
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    import jieba  # type: ignore
except Exception:  # pragma: no cover
    jieba = None

from ai_word_detector import (  # type: ignore
    AcademicCorpus,
    LanguageDetector,
    NGRAM_SEP,
    StyleAnalyzer,
    STOP_WORDS,
    STOP_WORDS_ZH,
    UDPipeSyntaxAnalyzer,
    is_heading_like,
    normalize_soft_line_breaks_preserve_len,
    split_sentences_with_positions,
)


def guess_language_for_sentence(text: str, *, fallback: str = "en") -> str:
    s = (text or "").strip()
    if not s:
        return fallback
    has_zh = bool(re.search(r"[\u4e00-\u9fff]", s))
    has_en = bool(re.search(r"[A-Za-z]", s))
    if has_zh and has_en:
        return "mixed"
    if has_zh:
        return "zh"
    if has_en:
        return "en"
    return fallback


def extract_scaffold(text: str, *, language: str) -> str:
    """
    Extract a short, copy-friendly "scaffold phrase" from an exemplar quote.
    This is a best-effort heuristic for white-box suggestions.
    """

    s0 = (text or "").strip()
    if not s0:
        return ""

    lang = (language or "").strip().lower() or "en"
    if lang not in ("en", "zh", "mixed"):
        lang = "en"

    if lang == "zh":
        s = re.sub(r"\s+", "", s0)
        head = re.split(r"[。！？；;]", s, maxsplit=1)[0].strip()
        head = re.sub(r"^[（(]?\d+(?:\.\d+)?[)）]?\s*", "", head).strip()
        if len(head) > 16:
            head = head[:16]
        return head or (s[:16] if len(s) > 16 else s)

    # English (or mixed): prioritize common academic openers / transitions.
    priority = [
        "in this paper",
        "in this study",
        "in this work",
        "in this article",
        "overall",
        "taken together",
        "consistent with",
        "in line with",
        "in particular",
        "specifically",
        "to this end",
        "more generally",
        "in addition",
        "moreover",
        "finally",
        "we examine",
        "we study",
        "we investigate",
        "we find",
        "we show",
        "we document",
        "we provide",
    ]

    # Strip common prefix like "[pdf#pX]" and remove leading labels ("Table 2:").
    s = re.sub(r"^\[[^\]]+\]\s*", "", s0).strip()
    s = re.sub(r"(?is)^\s*(?:table|figure|fig\\.|paper|appendix|section)\\s*\\d+\\s*[:.\\-–—]\\s*", "", s).strip()

    s_low = s.lower()
    for p in priority:
        j = s_low.find(p)
        if j < 0:
            continue
        cand = s[j : j + len(p)]
        try:
            if j + len(p) < len(s) and s[j + len(p)] in (",", "，"):
                cand = s[j : j + len(p) + 1]
        except Exception:
            pass
        cand = cand.strip()
        if cand and not re.search(r"\d", cand):
            return cand

    # Fallback: first 4-8 words, prefer digit-free.
    words = re.findall(r"\b[^\W\d_]+\b", s)
    if not words:
        return ""
    for k in (6, 8, 4):
        cand = " ".join(words[: min(k, len(words))]).strip()
        if cand and not re.search(r"\d", cand):
            return cand
    return " ".join(words[:6]).strip()


def extract_pdf_pages_text(
    pdf_path: str,
    *,
    max_pages: Optional[int] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict[str, Any]]:
    if fitz is None:
        raise RuntimeError("Missing dependency: PyMuPDF")
    path = (pdf_path or "").strip()
    if not path:
        raise ValueError("pdf_path required")
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    doc = fitz.open(path)
    try:
        total = int(getattr(doc, "page_count", 0) or 0)
        if total <= 0:
            return []
        limit = total
        if max_pages is not None:
            try:
                limit = max(1, min(total, int(max_pages)))
            except Exception:
                limit = total

        out: List[Dict[str, Any]] = []
        for i in range(limit):
            if cancel_cb and cancel_cb():
                break
            page_num = i + 1
            try:
                raw = doc[i].get_text("text") or ""
            except Exception:
                raw = ""
            text = normalize_soft_line_breaks_preserve_len(raw or "")
            out.append({"page": page_num, "text": text})
            if progress_cb:
                try:
                    progress_cb(page_num, limit, f"p{page_num}")
                except Exception:
                    pass
        return out
    finally:
        try:
            doc.close()
        except Exception:
            pass


@dataclass
class AuditIssue:
    issue_type: str
    severity: str
    description: str
    matched_text: str = ""


def _issue_to_dict(it: Any) -> Dict[str, Any]:
    if isinstance(it, AuditIssue):
        return {
            "issue_type": it.issue_type,
            "severity": it.severity,
            "description": it.description,
            "matched_text": it.matched_text,
        }
    if isinstance(it, dict):
        return {
            "issue_type": str(it.get("issue_type", "") or "").strip(),
            "severity": str(it.get("severity", "") or "").strip(),
            "description": str(it.get("description", "") or "").strip(),
            "matched_text": str(it.get("matched_text", "") or "").strip(),
        }
    # ai_word_detector.SentenceIssue
    return {
        "issue_type": str(getattr(it, "issue_type", "") or "").strip(),
        "severity": str(getattr(it, "severity", "") or "").strip(),
        "description": str(getattr(it, "description", "") or "").strip(),
        "matched_text": str(getattr(it, "matched_text", "") or "").strip(),
    }


def _tokenize_zh_light(s: str) -> List[str]:
    s0 = (s or "").strip()
    if not s0:
        return []
    if jieba is not None:
        try:
            toks = jieba.lcut(s0, cut_all=False)
        except Exception:
            toks = []
    else:
        toks = list(s0)
    out: List[str] = []
    for t in toks:
        t = (t or "").strip()
        if not t:
            continue
        if not re.fullmatch(r"[\u4e00-\u9fff]+", t):
            continue
        out.append(t)
    return out


def analyze_lexical_stats(
    sentences: List[Dict[str, Any]],
    *,
    corpus: Optional[AcademicCorpus],
) -> Dict[str, Any]:
    """
    Lightweight lexical diagnostics (no LLM).

    Returns:
      - top tokens in the paper (en/zh)
      - tokens that appear frequently in the paper but are rare in the exemplar corpus
    """
    if corpus is None:
        return {}

    doc_by_lang = getattr(corpus, "doc_count_by_lang", {}) or {}
    try:
        doc_en = int(doc_by_lang.get("en", 0) or 0) or int(getattr(corpus, "doc_count", 0) or 0) or 1
    except Exception:
        doc_en = 1
    try:
        doc_zh = int(doc_by_lang.get("zh", 0) or 0) or int(getattr(corpus, "doc_count", 0) or 0) or 1
    except Exception:
        doc_zh = 1

    wdf = getattr(corpus, "word_doc_freq", {}) or {}
    if not isinstance(wdf, dict):
        wdf = {}

    en_counts: Counter[str] = Counter()
    zh_counts: Counter[str] = Counter()
    en_total = 0
    zh_total = 0

    for it in sentences:
        s = str(it.get("text", "") or "").strip()
        if not s:
            continue
        lang = str(it.get("lang", "") or "en").strip().lower()
        if lang == "mixed":
            # Tokenize both streams.
            en_toks = re.findall(r"\b[a-z]+\b", s.lower())
            en_toks = [t for t in en_toks if len(t) >= 3 and t not in STOP_WORDS]
            if en_toks:
                en_counts.update(en_toks)
                en_total += len(en_toks)
            zh_toks = [t for t in _tokenize_zh_light(s) if len(t) >= 2 and t not in STOP_WORDS_ZH]
            if zh_toks:
                zh_counts.update(zh_toks)
                zh_total += len(zh_toks)
            continue

        if lang == "zh":
            toks = [t for t in _tokenize_zh_light(s) if len(t) >= 2 and t not in STOP_WORDS_ZH]
            if toks:
                zh_counts.update(toks)
                zh_total += len(toks)
            continue

        toks = re.findall(r"\b[a-z]+\b", s.lower())
        toks = [t for t in toks if len(t) >= 3 and t not in STOP_WORDS]
        if toks:
            en_counts.update(toks)
            en_total += len(toks)

    def _top(counter: Counter[str], n: int) -> List[Dict[str, Any]]:
        out = []
        for tok, cnt in counter.most_common(max(1, int(n))):
            out.append({"token": tok, "count": int(cnt)})
        return out

    def _rare(counter: Counter[str], *, min_count: int, doc_n: int, max_items: int = 30) -> List[Dict[str, Any]]:
        out = []
        for tok, cnt in counter.most_common(400):
            if int(cnt) < int(min_count):
                break
            df = 0
            try:
                df = int(wdf.get(tok, 0) or 0)
            except Exception:
                df = 0
            ratio = float(df) / float(max(1, int(doc_n)))
            # "rare in exemplars": appears in <= 2% of exemplar docs (or <= 1 doc for small corpora)
            if df <= 1 or ratio <= 0.02:
                out.append({"token": tok, "paper_count": int(cnt), "exemplar_doc_freq": int(df), "exemplar_doc_ratio": float(ratio)})
            if len(out) >= int(max_items):
                break
        return out

    return {
        "paper_token_total": {"en": int(en_total), "zh": int(zh_total)},
        "paper_top_tokens": {"en": _top(en_counts, 20), "zh": _top(zh_counts, 20)},
        "rare_in_exemplars": {
            "en": _rare(en_counts, min_count=8, doc_n=doc_en, max_items=30),
            "zh": _rare(zh_counts, min_count=5, doc_n=doc_zh, max_items=30),
        },
    }


def analyze_repetition_starters(
    sentences: List[Dict[str, Any]],
    *,
    language: str,
    min_repeat: int = 3,
) -> Dict[int, List[Dict[str, Any]]]:
    lang = (language or "").strip().lower() or "en"
    if lang not in ("en", "zh"):
        lang = "en"

    keys_by_id: Dict[int, str] = {}
    counts: Dict[str, int] = {}
    for it in sentences:
        try:
            sid = int(it.get("id", -1))
        except Exception:
            continue
        s = (it.get("text", "") or "").strip()
        if not s:
            continue
        if is_heading_like(s, lang):
            continue

        if lang == "en":
            toks = re.findall(r"\b[a-z]+\b", s.lower())
            if len(toks) < 6:
                continue
            key = " ".join(toks[:3]).strip()
        else:
            toks = _tokenize_zh_light(s)
            if len(toks) < 5:
                continue
            key = "".join(toks[:3]).strip()
        if not key:
            continue
        keys_by_id[sid] = key
        counts[key] = counts.get(key, 0) + 1

    repeated = {k for k, v in counts.items() if int(v) >= int(min_repeat)}
    out: Dict[int, List[Dict[str, Any]]] = {}
    for sid, key in keys_by_id.items():
        c = counts.get(key, 0)
        if key in repeated:
            out[sid] = [
                _issue_to_dict(
                    AuditIssue(
                        issue_type="repetition",
                        severity="info",
                        description=f"句子开头重复较多：{key}（出现 {int(c)} 次），容易显得模板化。",
                        matched_text=key,
                    )
                )
            ]
    return out


def analyze_syntax_outliers(
    sentences: List[Dict[str, Any]],
    *,
    language: str,
    corpus: Optional[AcademicCorpus],
    syntax_analyzer: Optional[UDPipeSyntaxAnalyzer],
) -> Dict[int, List[Dict[str, Any]]]:
    lang = (language or "").strip().lower() or "en"
    if lang not in ("en", "zh"):
        lang = "en"
    if corpus is None or syntax_analyzer is None:
        return {}
    try:
        if not corpus.has_syntax_stats(lang):
            return {}
    except Exception:
        return {}
    try:
        if not syntax_analyzer.has_lang(lang):
            return {}
    except Exception:
        return {}

    freq = getattr(corpus, "pos_bigram_sentence_freq", {}).get(lang, None)
    if not freq:
        return {}

    out: Dict[int, List[Dict[str, Any]]] = {}
    for it in sentences:
        try:
            sid = int(it.get("id", -1))
        except Exception:
            continue
        s = (it.get("text", "") or "").strip()
        if not s:
            continue
        if is_heading_like(s, lang):
            continue

        # Skip very short fragments
        if lang == "en":
            if len(re.findall(r"\b[a-z]+\b", s.lower())) < 8:
                continue
        else:
            if len(re.findall(r"[\u4e00-\u9fff]", s)) < 16:
                continue

        try:
            parsed = syntax_analyzer.analyze_sentence(s, lang)
        except Exception:
            parsed = None
        if not parsed or not isinstance(parsed, dict):
            continue

        upos = list(parsed.get("upos", []) or [])
        if len(upos) < 4:
            continue

        total_bg = 0
        unseen_bg = 0
        examples: List[str] = []
        for a, b in zip(upos, upos[1:]):
            if not a or not b:
                continue
            total_bg += 1
            key = f"{a}{NGRAM_SEP}{b}"
            if int(getattr(freq, "get", lambda _k, _d=0: 0)(key, 0) or 0) == 0:
                unseen_bg += 1
                if len(examples) < 2:
                    examples.append(key.replace(NGRAM_SEP, "→"))

        if total_bg >= 5 and unseen_bg >= 3 and (unseen_bg / max(1, total_bg)) >= 0.55:
            example = examples[0] if examples else ""
            out[sid] = [
                _issue_to_dict(
                    AuditIssue(
                        issue_type="syntax_outlier",
                        severity="warning",
                        description=f"句法/词性搭配在范文库里较少见（异常 POS bigram：{example}）。可考虑调整语序或拆分句子。",
                        matched_text=example,
                    )
                )
            ]
    return out


def run_full_paper_audit(
    *,
    paper_pdf_path: str,
    exemplar_library: str,
    search_exemplars: Callable[[str, int], List[Tuple[float, Dict[str, Any]]]],
    corpus: Optional[AcademicCorpus] = None,
    syntax_analyzer: Optional[UDPipeSyntaxAnalyzer] = None,
    max_pages: Optional[int] = None,
    max_sentences: int = 360,
    min_sentence_len: int = 20,
    top_k: int = 4,
    low_alignment_threshold: float = 0.35,
    include_style: bool = True,
    include_repetition: bool = True,
    include_syntax: bool = True,
    cancel_cb: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    started = time.time()

    pages = extract_pdf_pages_text(
        paper_pdf_path,
        max_pages=max_pages,
        cancel_cb=cancel_cb,
        progress_cb=(lambda d, t, detail: progress_cb("audit_extract", int(d), int(t), detail) if progress_cb else None),
    )
    full_head = "\n".join([p.get("text", "") or "" for p in pages[: min(3, len(pages))]])
    primary_lang = "en"
    try:
        primary_lang = LanguageDetector.detect(full_head or "")
    except Exception:
        primary_lang = "en"
    if primary_lang not in ("en", "zh", "mixed"):
        primary_lang = "en"

    # Prepare style analyzer baseline from corpus.
    style = None
    if include_style:
        try:
            # Best-effort load AI words list (optional).
            ai_words_data = {}
            try:
                from ai_word_detector import get_resource_path  # type: ignore

                p = get_resource_path("word_lists/ai_words_zh.json")
                if p and os.path.exists(p):
                    with open(p, "r", encoding="utf-8") as f:
                        ai_words_data = json.load(f)
            except Exception:
                ai_words_data = {}

            style = StyleAnalyzer(ai_words_data, language=primary_lang if primary_lang != "mixed" else "zh")
            if corpus is not None:
                try:
                    for l in ("en", "zh"):
                        rs = getattr(corpus, "sentence_length_stats", {}).get(l, None)
                        avg = float(getattr(rs, "mean", 0.0) or 0.0) if rs is not None else 0.0
                        if avg > 0:
                            style.set_corpus_stats(avg, l)
                except Exception:
                    pass
        except Exception:
            style = None

    # Split sentences per page.
    sentences: List[Dict[str, Any]] = []
    sid = 0
    for p in pages:
        if cancel_cb and cancel_cb():
            break
        page = int(p.get("page", 0) or 0) or 0
        txt = str(p.get("text", "") or "")
        if not txt.strip():
            continue
        # Language for sentence splitting: prefer per-page detection when mixed.
        lang = primary_lang
        if primary_lang == "mixed":
            try:
                lang = LanguageDetector.detect(txt)
            except Exception:
                lang = "mixed"
            if lang not in ("en", "zh", "mixed"):
                lang = "mixed"
        try:
            sents = split_sentences_with_positions(txt, lang)
        except Exception:
            sents = []
        for sent, _s, _e in sents:
            st = (sent or "").strip()
            if not st:
                continue
            sentences.append(
                {
                    "id": sid,
                    "page": page,
                    "text": st,
                    "lang": guess_language_for_sentence(st, fallback=lang if lang != "mixed" else "en"),
                }
            )
            sid += 1
            if sid % 400 == 0 and progress_cb:
                try:
                    progress_cb("audit_split", sid, max(1, sid), f"s{sid}")
                except Exception:
                    pass

    total_sentences = len(sentences)

    # Select sentences for alignment scoring (sample if too many).
    candidates = []
    for it in sentences:
        s = (it.get("text", "") or "").strip()
        lang = (it.get("lang", "") or "").strip().lower() or primary_lang
        if not s:
            continue
        if len(s) < int(min_sentence_len):
            continue
        if is_heading_like(s, lang if lang in ("en", "zh", "mixed") else primary_lang):
            continue
        candidates.append(it)

    sampled = candidates
    truncated = False
    try:
        max_sentences = max(50, min(int(max_sentences), 3600))
    except Exception:
        max_sentences = 360
    if len(sampled) > max_sentences:
        truncated = True
        step = float(len(sampled)) / float(max_sentences)
        pick = []
        for i in range(max_sentences):
            j = int(round(i * step))
            if j < 0:
                j = 0
            if j >= len(sampled):
                j = len(sampled) - 1
            pick.append(sampled[j])
        # De-dup while preserving order.
        seen = set()
        sampled2 = []
        for it in pick:
            sid2 = int(it.get("id", -1))
            if sid2 in seen:
                continue
            seen.add(sid2)
            sampled2.append(it)
        sampled = sampled2

    if progress_cb:
        try:
            progress_cb("audit_align", 0, len(sampled), "start")
        except Exception:
            pass

    # Alignment search
    align_by_id: Dict[int, Dict[str, Any]] = {}
    for i, it in enumerate(sampled, start=1):
        if cancel_cb and cancel_cb():
            break
        s = (it.get("text", "") or "").strip()
        sid2 = int(it.get("id", -1))
        results = []
        try:
            results = search_exemplars(s, int(top_k))
        except Exception:
            results = []
        exemplars = []
        best = 0.0
        for sc, ex in (results or [])[: max(1, int(top_k))]:
            try:
                score = float(sc or 0.0)
            except Exception:
                score = 0.0
            if score > best:
                best = score
            pdf = str((ex or {}).get("pdf", "") or "")
            try:
                page = int((ex or {}).get("page", 0) or 0)
            except Exception:
                page = 0
            txt = str((ex or {}).get("text", "") or "").strip()
            if len(txt) > 650:
                txt = txt[:650].rstrip() + "…"
            ex_lang = primary_lang if primary_lang != "mixed" else guess_language_for_sentence(txt, fallback="en")
            exemplars.append(
                {
                    "score": score,
                    "pct": int(max(0.0, min(1.0, score)) * 100),
                    "pdf": pdf,
                    "page": page,
                    "text": txt,
                    "scaffold": extract_scaffold(txt, language=ex_lang),
                }
            )

        align_by_id[sid2] = {
            "score": best,
            "pct": int(max(0.0, min(1.0, best)) * 100),
            "exemplars": exemplars,
        }
        if progress_cb and i % 5 == 0:
            try:
                progress_cb("audit_align", i, len(sampled), f"s{sid2}")
            except Exception:
                pass

    if progress_cb:
        try:
            progress_cb("audit_align", len(sampled), len(sampled), "done")
        except Exception:
            pass

    # Style issues (cheap) for all sentences.
    issues_by_id: Dict[int, List[Dict[str, Any]]] = {}
    if style is not None:
        if progress_cb:
            try:
                progress_cb("audit_style", 0, total_sentences, "start")
            except Exception:
                pass
        for i, it in enumerate(sentences, start=1):
            if cancel_cb and cancel_cb():
                break
            s = (it.get("text", "") or "").strip()
            sid2 = int(it.get("id", -1))
            lang = str((it.get("lang", "") or primary_lang) or primary_lang).strip().lower()
            if not s:
                continue
            try:
                local = style._check_sentence(s, lang if lang in ("en", "zh", "mixed") else primary_lang)  # type: ignore[attr-defined]
            except Exception:
                local = []
            if local:
                issues_by_id.setdefault(sid2, [])
                for iss in local:
                    issues_by_id[sid2].append(_issue_to_dict(iss))
            if progress_cb and i % 200 == 0:
                try:
                    progress_cb("audit_style", i, total_sentences, f"s{sid2}")
                except Exception:
                    pass
        if progress_cb:
            try:
                progress_cb("audit_style", total_sentences, total_sentences, "done")
            except Exception:
                pass

    # Repetition issues.
    if include_repetition:
        rep = analyze_repetition_starters(sentences, language=primary_lang if primary_lang != "mixed" else "en")
        for sid2, iss in rep.items():
            issues_by_id.setdefault(sid2, []).extend(list(iss or []))

    # Syntax outliers: run only on problematic candidates to save time.
    if include_syntax and corpus is not None and syntax_analyzer is not None:
        focus = []
        for it in sentences:
            sid2 = int(it.get("id", -1))
            if sid2 < 0:
                continue
            has_style = bool(issues_by_id.get(sid2))
            align = align_by_id.get(sid2, None)
            low = bool(align and float(align.get("score", 0.0) or 0.0) < float(low_alignment_threshold))
            if has_style or low:
                focus.append(it)
        syn = analyze_syntax_outliers(focus, language=primary_lang if primary_lang != "mixed" else "en", corpus=corpus, syntax_analyzer=syntax_analyzer)
        for sid2, iss in syn.items():
            issues_by_id.setdefault(sid2, []).extend(list(iss or []))

    # Build final items list (only sentences that have any issue or were scored and are low-alignment).
    items = []
    low_align = 0
    style_issue_sent = 0
    issue_counts: Dict[str, int] = {}

    for it in sentences:
        sid2 = int(it.get("id", -1))
        if sid2 < 0:
            continue
        align = align_by_id.get(sid2, None)
        score = float(align.get("score", 0.0) or 0.0) if isinstance(align, dict) else 0.0
        is_low = bool(isinstance(align, dict) and score < float(low_alignment_threshold))
        local_issues = list(issues_by_id.get(sid2, []) or [])
        if not local_issues and not is_low:
            continue

        if is_low:
            low_align += 1
            local_issues.append(
                _issue_to_dict(
                    AuditIssue(
                        issue_type="low_alignment",
                        severity="warning",
                        description=f"与范文库对齐度偏低（相似度 {int(max(0.0, min(1.0, score)) * 100)}%）。建议参考右侧范文片段的句式骨架改写。",
                        matched_text="",
                    )
                )
            )

        has_style = any(str(x.get("issue_type", "")) in ("ai_transition", "ai_word", "template", "passive", "long_sentence") for x in local_issues)
        if has_style:
            style_issue_sent += 1

        for x in local_issues:
            k = str(x.get("issue_type", "") or "").strip()
            if k:
                issue_counts[k] = issue_counts.get(k, 0) + 1

        suggestions = []
        if isinstance(align, dict):
            exs = list(align.get("exemplars", []) or [])
            if exs:
                scaff = str(exs[0].get("scaffold", "") or "").strip()
                if scaff:
                    suggestions.append(
                        {
                            "kind": "scaffold",
                            "text": scaff,
                            "from": {"pdf": exs[0].get("pdf", ""), "page": exs[0].get("page", 0)},
                        }
                    )

        items.append(
            {
                "id": sid2,
                "page": int(it.get("page", 0) or 0),
                "text": str(it.get("text", "") or ""),
                "lang": str(it.get("lang", "") or primary_lang),
                "alignment": align or {"score": 0.0, "pct": 0, "exemplars": []},
                "issues": local_issues,
                "suggestions": suggestions,
            }
        )

    items.sort(key=lambda x: (0 if any(i.get("issue_type") == "low_alignment" for i in (x.get("issues") or [])) else 1, float((x.get("alignment") or {}).get("score", 0.0) or 0.0)))

    elapsed_s = time.time() - started
    lexical = {}
    try:
        lexical = analyze_lexical_stats(sentences, corpus=corpus)
    except Exception:
        lexical = {}
    return {
        "meta": {
            "paper_pdf_path": os.path.abspath(paper_pdf_path),
            "paper_pages": len(pages),
            "language": primary_lang,
            "exemplar_library": exemplar_library,
            "created_at": int(time.time()),
            "elapsed_s": float(elapsed_s),
            "truncated": bool(truncated),
            "limits": {
                "max_pages": max_pages,
                "max_sentences": int(max_sentences),
                "min_sentence_len": int(min_sentence_len),
                "top_k": int(top_k),
                "low_alignment_threshold": float(low_alignment_threshold),
            },
        },
        "summary": {
            "sentence_total": int(total_sentences),
            "sentence_scored": int(len(sampled)),
            "low_alignment_sentences": int(low_align),
            "style_issue_sentences": int(style_issue_sent),
            "issue_counts": issue_counts,
        },
        "lexical": lexical,
        "items": items,
    }
