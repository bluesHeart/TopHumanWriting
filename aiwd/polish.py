# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Citation:
    id: str
    pdf: str
    page: int
    quote: str


@dataclass(frozen=True)
class DiagnosisItem:
    title: str
    problem: str
    suggestion: str
    evidence: List[Citation]


@dataclass(frozen=True)
class RewriteVariant:
    level: str  # "light" | "medium"
    rewrite: str
    changes: List[str]
    citations: List[Citation]


@dataclass(frozen=True)
class PolishResult:
    language: str  # "zh" | "en" | "mixed"
    diagnosis: List[DiagnosisItem]
    variants: List[RewriteVariant]


class PolishValidationError(ValueError):
    pass


def build_polish_prompt(
    *,
    selected_text: str,
    citations: Sequence[Tuple[str, str]],
    language: str,
    compact: bool = False,
) -> str:
    """
    citations: list of (id, text) where id is like "C1" and text is exemplar chunk.
    """
    if compact:
        rules = [
            "You are a writing editor. Rewrite USER_TEXT to better match EXEMPLARS style while preserving meaning.",
            "STYLE ALIGNMENT PRIORITY: sentence-level alignment (openers, transitions, clause order, academic phrasing). Borrow short scaffold phrases from exemplars (NOT full sentences).",
            "SCAFFOLD PHRASES: choose a reusable substring you can paste verbatim into your rewrites (avoid subject/tense changes like examine->examines). Prefer neutral chunks like \"in this paper\", \"we contribute by\", \"consistent with\", \"overall\".",
            "NO META: do not mention AI/models or add disclaimers/apologies.",
            "PRESERVE VOICE: do not change narrative perspective (do not introduce 'we/our/I' if absent in USER_TEXT).",
            "Do NOT add new facts/claims/citations/numbers/entities. Do NOT introduce any digits unless already in USER_TEXT.",
            "Only cite from C1..Ck. WHITE-BOX: every diagnosis + rewrite must include evidence quotes that are exact substrings of the provided excerpts.",
            "Return 3 diagnosis items (actionable; include Scaffold: \"...\" copied verbatim from evidence). If you cannot make 3, return as many as you can (>=1).",
            "Return exactly 2 rewrites: level='light' and level='medium'. Each rewrite must include at least ONE scaffold phrase from exemplars (generic phrasing only).",
            "STRICT JSON ONLY (no markdown). No raw newlines inside JSON strings (replace with spaces).",
            "LANGUAGE: follow LANGUAGE_HINT; write diagnosis + rewrites in the same language as USER_TEXT.",
        ]
    else:
        rules = [
            "You are a writing editor. Your job is to rewrite the user's text to better match the style of the exemplar excerpts.",
            "Goal: emulate exemplar writing style (structure, tone, academic phrasing, transitions) while preserving meaning.",
            "STYLE ALIGNMENT PRIORITY: focus on sentence-level alignment (openers, transitions, clause order, active/passive voice, nominalization). Prefer borrowing short scaffold phrases from exemplars (NOT full sentences).",
            "SCAFFOLD PHRASES: choose a reusable substring you can paste verbatim into your rewrites (avoid subject/tense changes like examine->examines). Prefer neutral chunks like \"in this paper\", \"we contribute by\", \"consistent with\", \"overall\".",
            "NO META: do not mention AI, models, or provide disclaimers/apologies. Output only the requested JSON content.",
            "PRESERVE VOICE: do not change the narrative perspective (do not introduce 'we/our/I' if absent in USER_TEXT).",
            "Do NOT add new facts, new claims, new citations, new numbers, or new named entities.",
            "CRITICAL: do NOT introduce any digits (0-9) or year-like citations in rewrites unless they already appear in USER_TEXT. When borrowing scaffold phrases, exclude author/year parts and keep it generic.",
            "Only cite from the provided excerpts C1..Ck; do not invent sources.",
            "WHITE-BOX REQUIREMENT: every diagnosis item must be supported by at least one evidence quote copied verbatim from the provided excerpts.",
            "Return 3 diagnosis items: what is not exemplar-like + how to adjust to match exemplars, each with evidence. If you cannot make 3, return as many as you can (>=1). Prefer at least 2 items about sentence structure/phrasing patterns (not just word choice).",
            "DIAGNOSIS MUST BE ACTIONABLE: avoid vague advice like “use academic language”. Each suggestion must include 1-2 concrete scaffold phrases (3-12 words) that appear verbatim in the evidence quote, and explain how to use them (no author names/years unless already in USER_TEXT).",
            "SUGGESTION FORMAT (required): start suggestion with `Scaffold: \"...\"` (copy a reusable scaffold phrase from evidence), then explain the rewrite move in 1 sentence.",
            "Return exactly TWO rewrites: one with level='light' and one with level='medium'.",
            "Light: minimal wording/flow edits. Medium: more rephrasing to match exemplar tone, still preserve meaning.",
            "REWRITE MUST SHOW STYLE ALIGNMENT: each rewrite must incorporate at least ONE scaffold phrase borrowed from the exemplars (generic phrasing only; do not copy author names/years).",
            "For citations/evidence: quote MUST be an exact substring from the corresponding exemplar text.",
            "EVIDENCE QUOTE QUALITY: keep each quote short (<= 180 chars), and prefer quoting the generic scaffold snippet only (avoid author/year/digits when possible).",
            "CITATIONS ARE REQUIRED: every diagnosis item must include evidence; every rewrite variant must include 1-4 citations.",
            "STRICTNESS: output must be valid JSON (no markdown). Ensure brackets/quotes are closed; do not include raw newlines inside JSON strings (replace line breaks with spaces).",
            "CONCISENESS: keep title <= 12 words, problem/suggestion <= 2 sentences, changes <= 8 bullets total, each <= 18 words.",
            "LANGUAGE: follow LANGUAGE_HINT strictly; write diagnosis + rewrites in the same language as USER_TEXT (zh/en/mixed).",
            "Return STRICT JSON only (no markdown), matching the schema described.",
        ]
    schema = {
        "language": "zh|en|mixed",
        "diagnosis": [
            {
                "title": "...",
                "problem": "...",
                "suggestion": "...",
                "evidence": [{"id": "C1", "pdf": "path.pdf", "page": 1, "quote": "exact excerpt substring"}],
            }
        ],
        "variants": [
            {
                "level": "light|medium",
                "rewrite": "...",
                "changes": ["..."],
                "citations": [{"id": "C1", "pdf": "path.pdf", "page": 1, "quote": "exact excerpt substring"}],
            }
        ],
    }
    parts = []
    parts.append("RULES:\n- " + "\n- ".join(rules))
    parts.append("OUTPUT_SCHEMA:\n" + json.dumps(schema, ensure_ascii=False))
    parts.append(f"LANGUAGE_HINT: {language}")
    parts.append("USER_TEXT:\n" + (selected_text or "").strip())
    parts.append("EXEMPLARS:")
    for cid, ctext in citations:
        parts.append(f"{cid}:\n{(ctext or '').strip()}")
    return "\n\n".join(parts).strip()


def extract_json(text: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None
    s = text.strip()
    if not s:
        return None
    # Remove common Markdown fences.
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s, flags=re.IGNORECASE)

    # Find the first object start.
    start = s.find("{")
    if start < 0:
        return None
    s2 = s[start:]

    # Many providers occasionally emit raw newlines inside JSON strings. Replace with spaces
    # before parsing so the JSON becomes parseable while keeping substring constraints sane.
    s2 = s2.replace("\r", "\n").replace("\n", " ")

    dec = json.JSONDecoder()
    try:
        obj, _end = dec.raw_decode(s2)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def validate_polish_json(
    data: dict,
    *,
    allowed_citation_ids: Sequence[str],
    allowed_quotes: Dict[str, str],
    selected_text: Optional[str] = None,
) -> PolishResult:
    if not isinstance(data, dict):
        raise PolishValidationError("not an object")

    selected_text = (selected_text or "").strip()
    base_numbers = set(re.findall(r"\d+(?:\.\d+)?", selected_text)) if selected_text else set()
    base_has_square = ("[" in selected_text) or ("]" in selected_text)

    lang = (data.get("language", "") or "").strip().lower()
    if lang not in ("zh", "en", "mixed"):
        lang = "mixed"

    variants_raw = data.get("variants", [])
    if not isinstance(variants_raw, list) or not variants_raw:
        raise PolishValidationError("variants missing")

    allowed_ids = {str(x) for x in allowed_citation_ids if str(x)}
    allowed_meta: Dict[str, Tuple[str, int]] = {}
    for cid, excerpt in allowed_quotes.items():
        if not cid:
            continue
        m = re.match(r"^\[(?P<pdf>.+?)#p(?P<page>\d+)\]\s*", (excerpt or "").strip())
        if not m:
            continue
        pdf = (m.group("pdf") or "").strip().replace("\\", "/")
        try:
            page = int(m.group("page") or "0")
        except Exception:
            page = 0
        if pdf and page > 0:
            allowed_meta[str(cid)] = (pdf, page)

    def _fallback_citations(max_items: int = 2) -> List[Citation]:
        out: List[Citation] = []
        for cid in allowed_citation_ids:
            cid = str(cid).strip()
            if not cid:
                continue
            allowed_text = (allowed_quotes.get(cid, "") or "").strip()
            if not allowed_text:
                continue
            # Prefer quoting the excerpt body (without the [pdf#pX] prefix) to keep it short.
            body = allowed_text
            m = re.match(r"^\[[^\]]+\]\s*(.*)$", allowed_text)
            if m:
                body = (m.group(1) or "").strip()
            quote = (body or allowed_text).strip()
            if not quote:
                continue
            if len(quote) > 220:
                quote = quote[:220].rstrip()
            # Ensure quote is a substring of the original allowed text (white-box).
            if quote not in allowed_text:
                continue
            pdf, page = "", 0
            want = allowed_meta.get(cid, None)
            if want:
                pdf, page = want
            out.append(Citation(id=cid, pdf=pdf or "", page=int(page or 0), quote=quote))
            if len(out) >= int(max_items):
                break
        return out

    def _extract_scaffold_from_quote(quote: str) -> str:
        q0 = (quote or "").strip()
        if not q0:
            return ""
        if lang == "zh":
            # Prefer the first clause before punctuation, keep it short.
            head = re.split(r"[。！？；;]", q0, maxsplit=1)[0].strip()
            head = re.sub(r"\s+", "", head)
            if len(head) > 16:
                head = head[:16]
            return head or q0[:16]

        # en / mixed: prefer a reusable prefix before citation-like parentheses.
        q = q0
        m = re.search(r"\([^)]*\d[^)]*\)", q)
        if m:
            q = q[: m.start()].strip()
        q = re.split(r"[.!?;:]", q, maxsplit=1)[0].strip()
        q = re.sub(r"\s+", " ", q).strip()
        if not q:
            q = q0
        words = q.split()
        if len(words) > 12:
            words = words[:12]

        # If a proper-name-looking token appears after a preposition, cut before it.
        for i in range(1, len(words)):
            w = words[i]
            prev = words[i - 1].lower()
            if re.match(r"^[A-Z][a-z]+", w) and prev in ("in", "by", "from", "to", "with", "on", "at", "of", "for"):
                words = words[:i]
                break
            if i == 1 and re.match(r"^[A-Z][a-z]+", w):
                # e.g., "Following Smith ..." -> keep only the opener.
                words = words[:1]
                break
        return " ".join(words).strip()

    def _ensure_scaffold_in_suggestion(suggestion: str, evidence_quotes: Sequence[str]) -> str:
        suggestion = (suggestion or "").strip()
        ev_list = [str(x or "") for x in (evidence_quotes or []) if str(x or "").strip()]
        want_scaffold = _extract_scaffold_from_quote(ev_list[0]) if ev_list else ""

        # Try to parse existing scaffold.
        m = re.search(r"(?i)\bscaffold\s*:\s*[\"'](?P<p>[^\"']+)[\"']", suggestion)
        if m:
            cur = (m.group("p") or "").strip()
            if cur and any(cur in ev for ev in ev_list):
                return suggestion
            if want_scaffold:
                suggestion = suggestion[: m.start()] + f'Scaffold: "{want_scaffold}"' + suggestion[m.end() :]
        else:
            if want_scaffold:
                suggestion = f'Scaffold: "{want_scaffold}" ' + suggestion

        # Ensure there is an explanation after the scaffold.
        if re.fullmatch(r"(?i)\s*scaffold\s*:\s*[\"'][^\"']+[\"']\s*", suggestion) or len(suggestion) < 24:
            if lang == "zh":
                suggestion = suggestion.strip() + " 用这个句式骨架调整语序与衔接，使表达更像范文。"
            else:
                suggestion = suggestion.strip() + " Use this scaffold to adjust clause order and transitions to match the exemplars."
        return suggestion.strip()

    diagnosis: List[DiagnosisItem] = []

    def _looks_placeholder(s: str) -> bool:
        s = (s or "").strip()
        if not s:
            return True
        if re.fullmatch(r"[.\u2026…]+", s):
            return True
        if s.lower() in ("todo", "tbd", "n/a", "na"):
            return True
        return False

    def _min_meaningful_len(s: str) -> int:
        # Rough heuristic that works for zh/en/mixed (remove punctuation/whitespace).
        s2 = re.sub(r"[\s\r\n\t\"'`.,;:!?()\[\]{}<>/\\\\|=_+\-—–…·•]+", "", (s or ""))
        return len(s2)

    diagnosis_raw = data.get("diagnosis", [])
    if isinstance(diagnosis_raw, list):
        for it in diagnosis_raw:
            if not isinstance(it, dict):
                continue
            title = (it.get("title", "") or "").strip()
            problem = (it.get("problem", "") or "").strip()
            suggestion = (it.get("suggestion", "") or "").strip()
            if _looks_placeholder(title) or _min_meaningful_len(title) < 3:
                continue
            if _looks_placeholder(suggestion) or _min_meaningful_len(suggestion) < 8:
                continue
            if _looks_placeholder(problem):
                problem = ""
            ev_raw = it.get("evidence", [])
            ev: List[Citation] = []
            if isinstance(ev_raw, list):
                for c in ev_raw:
                    if not isinstance(c, dict):
                        continue
                    cid = (c.get("id", "") or "").strip()
                    if cid not in allowed_ids:
                        continue
                    pdf = (c.get("pdf", "") or "").strip().replace("\\", "/")
                    try:
                        page = int(c.get("page", 0) or 0)
                    except Exception:
                        page = 0
                    quote = (c.get("quote", "") or "").strip()
                    allowed_text = allowed_quotes.get(cid, "")
                    if not quote:
                        continue
                    if allowed_text and quote not in allowed_text:
                        continue
                    want = allowed_meta.get(cid, None)
                    if want:
                        want_pdf, want_page = want
                        if want_page > 0:
                            if int(page or 0) == 0:
                                page = int(want_page)
                            elif int(page or 0) != int(want_page):
                                continue
                        if want_pdf:
                            if not pdf:
                                pdf = want_pdf
                            elif pdf != want_pdf:
                                continue
                    ev.append(Citation(id=cid, pdf=pdf, page=page, quote=quote))

            if title and suggestion and not ev:
                ev = _fallback_citations(max_items=1)
            if title and suggestion and ev:
                suggestion2 = _ensure_scaffold_in_suggestion(suggestion, [c.quote for c in ev])
                diagnosis.append(DiagnosisItem(title=title, problem=problem, suggestion=suggestion2, evidence=ev))

    def _fallback_diagnosis_templates() -> List[Tuple[str, str, str]]:
        if lang == "zh":
            return [
                ("句首/引出方式更像范文", "当前句子开头偏直白，缺少范文常见的铺垫与定位。", 'Scaffold: "" 用该句式做开头，先定位对象/范围，再给出核心信息。'),
                ("衔接与逻辑过渡", "句间过渡较弱，读者难以把握承接/转折/因果关系。", 'Scaffold: "" 在关键处加入过渡短语，明确逻辑关系并调整语序。'),
                ("表达更凝练、更学术", "措辞略分散或偏口语，信息密度不够。", 'Scaffold: "" 适度名词化/客观化并压缩冗余，使表达更像范文。'),
            ]
        # en / mixed
        return [
            ("More academic opener", "The sentence opens abruptly and lacks the academic framing in the exemplars.", 'Scaffold: "" Use this scaffold as the opener to frame scope before the main claim.'),
            ("Smoother transitions", "Transitions are weak, making the logical relation less explicit.", 'Scaffold: "" Add a transition phrase and align clause order with the exemplars.'),
            ("More concise academic phrasing", "Phrasing is verbose or conversational compared to the exemplars.", 'Scaffold: "" Replace informal wording with concise academic constructions without adding new facts.'),
        ]

    def _fill_missing_diagnosis(items: List[DiagnosisItem]) -> List[DiagnosisItem]:
        out = list(items or [])
        if len(out) >= 3:
            return out[:3]

        fb = _fallback_citations(max_items=3)
        if not fb:
            return out

        templates = _fallback_diagnosis_templates()
        used_titles = {d.title for d in out if d and d.title}
        i = 0
        while len(out) < 3:
            title0, problem0, suggestion0 = templates[len(out) % len(templates)]
            title = title0
            if title in used_titles:
                j = 2
                while f"{title0} ({j})" in used_titles:
                    j += 1
                title = f"{title0} ({j})"
            ev = [fb[i % len(fb)]]
            i += 1
            suggestion2 = _ensure_scaffold_in_suggestion(suggestion0, [c.quote for c in ev])
            out.append(DiagnosisItem(title=title, problem=problem0, suggestion=suggestion2, evidence=ev))
            used_titles.add(title)
        return out[:3]

    # Ensure stable, user-friendly diagnostics: always 3 actionable items (fill deterministically).
    if len(diagnosis) < 3:
        diagnosis = _fill_missing_diagnosis(diagnosis)
    if len(diagnosis) > 3:
        diagnosis = diagnosis[:3]

    # Extract scaffold phrases once (used to enforce "style alignment" in rewrites).
    scaffolds: List[str] = []
    for d in diagnosis:
        m = re.findall(r"(?i)\bscaffold\s*:\s*[\"']([^\"']+)[\"']", d.suggestion or "")
        for p in m:
            p = (p or "").strip()
            if p and p not in scaffolds:
                scaffolds.append(p)

    out_variants: List[RewriteVariant] = []

    for v in variants_raw:
        if not isinstance(v, dict):
            continue
        level = (v.get("level", "") or "").strip().lower()
        if level not in ("light", "medium"):
            continue
        rewrite = (v.get("rewrite", "") or "").strip()
        if not rewrite:
            continue
        if _looks_placeholder(rewrite) or _min_meaningful_len(rewrite) < 12:
            continue

        if selected_text:
            nums = set(re.findall(r"\d+(?:\.\d+)?", rewrite))
            new_nums = sorted(x for x in nums if x not in base_numbers)
            if new_nums:
                raise PolishValidationError("new numbers are not allowed")
            if not base_has_square and (("[" in rewrite) or ("]" in rewrite)):
                raise PolishValidationError("new bracket citations are not allowed")

        changes = v.get("changes", [])
        if not isinstance(changes, list):
            changes = []
        changes2 = [str(x).strip() for x in changes if str(x).strip()]

        cits = []
        cits_raw = v.get("citations", [])
        if isinstance(cits_raw, list):
            for c in cits_raw:
                if not isinstance(c, dict):
                    continue
                cid = (c.get("id", "") or "").strip()
                if cid not in allowed_ids:
                    continue
                pdf = (c.get("pdf", "") or "").strip().replace("\\", "/")
                try:
                    page = int(c.get("page", 0) or 0)
                except Exception:
                    page = 0
                quote = (c.get("quote", "") or "").strip()
                allowed_text = allowed_quotes.get(cid, "")
                if not quote:
                    continue
                if allowed_text and quote not in allowed_text:
                    continue

                want = allowed_meta.get(cid, None)
                if want:
                    want_pdf, want_page = want
                    if want_page > 0:
                        if int(page or 0) == 0:
                            page = int(want_page)
                        elif int(page or 0) != int(want_page):
                            continue
                    if want_pdf:
                        if not pdf:
                            pdf = want_pdf
                        elif pdf != want_pdf:
                            continue
                cits.append(Citation(id=cid, pdf=pdf, page=page, quote=quote))

        if not cits:
            cits = _fallback_citations(max_items=2)
        if not cits:
            raise PolishValidationError("missing citations")

        if scaffolds:
            # Soft constraint: local 3B models can miss scaffold insertion. Do not hard-fail.
            hit = False
            if lang == "zh":
                for p in scaffolds:
                    if p and p in rewrite:
                        hit = True
                        break
            else:
                rw_low = rewrite.lower()
                for p in scaffolds:
                    p0 = (p or "").strip()
                    if not p0:
                        continue
                    p_low = p0.lower()
                    candidates = [p_low]
                    for prefix in ("we ", "this paper ", "the paper ", "our paper ", "this study ", "the study "):
                        if p_low.startswith(prefix):
                            candidates.append(p_low[len(prefix) :].lstrip())
                    # Also allow stripping a leading determiner-like phrase (helps voice preservation).
                    candidates = [c.strip(" .,:;!?\t\r\n") for c in candidates if c.strip()]
                    if any(c and c in rw_low for c in candidates):
                        hit = True
                        break
            if not hit:
                note = (
                    "本次改写未直接使用上方“句式模板”（可复制插入后再生成/微调）。"
                    if lang == "zh"
                    else "This rewrite did not directly use a scaffold phrase (copy one above and retry if needed)."
                )
                if note not in changes2 and len(changes2) < 8:
                    changes2.append(note)

        out_variants.append(RewriteVariant(level=level, rewrite=rewrite, changes=changes2, citations=cits))

    if not out_variants:
        # Last-resort fallback: keep UX unblocked even when the model output contains no usable variants.
        fb = _fallback_citations(max_items=2)
        note = (
            "生成未包含改写文本：已保底返回原文。你仍可使用上方“句式模板”手动替换，或提高 max_tokens 后重试。"
            if lang == "zh"
            else "Rewrite text missing; returning original as fallback. Increase max_tokens and retry if needed."
        )
        base = selected_text or ""
        out_variants = [
            RewriteVariant(level="light", rewrite=base, changes=[note], citations=fb),
            RewriteVariant(level="medium", rewrite=base, changes=[note], citations=fb),
        ]

    # Deduplicate by level (keep first).
    seen = set()
    uniq: List[RewriteVariant] = []
    for v in out_variants:
        if v.level in seen:
            continue
        seen.add(v.level)
        uniq.append(v)

    # Ensure both levels exist for UI consistency (fill with original text when missing).
    have = {v.level for v in uniq}
    fb = _fallback_citations(max_items=2)
    base = selected_text or ""
    if "light" not in have:
        uniq.insert(
            0,
            RewriteVariant(
                level="light",
                rewrite=base,
                changes=[("缺少轻改输出：已返回原文。" if lang == "zh" else "Missing light variant; returned original.")],
                citations=fb,
            ),
        )
    if "medium" not in have:
        uniq.append(
            RewriteVariant(
                level="medium",
                rewrite=base,
                changes=[("缺少中改输出：已返回原文。" if lang == "zh" else "Missing medium variant; returned original.")],
                citations=fb,
            )
        )

    return PolishResult(language=lang, diagnosis=diagnosis, variants=uniq)
