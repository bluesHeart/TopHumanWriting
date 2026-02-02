# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from aiwd.llm_budget import LLMBudget, approx_tokens
from aiwd.openai_compat import OpenAICompatClient, extract_first_content, extract_usage
from aiwd.polish import extract_json
from aiwd.review_coverage import ReviewCoverageStore, stable_text_key


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _norm_evidence_id(ev_id: str) -> str:
    s = str(ev_id or "").strip()
    if not s:
        return ""
    s = s.replace("-", "_")
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"[^A-Za-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s)
    return s.upper()


def _resolve_allowed_id(allowed: Dict[str, str], *, ev_id: str) -> str:
    raw = str(ev_id or "").strip()
    if not raw:
        return ""
    if raw in allowed and allowed.get(raw, ""):
        return raw
    want = _norm_evidence_id(raw)
    if not want:
        return ""
    for k in (allowed or {}).keys():
        if _norm_evidence_id(k) == want and allowed.get(k, ""):
            return k
    return ""


def _evidence_id_ok(allowed: Dict[str, str], *, ev_id: str) -> bool:
    ev_id = str(ev_id or "").strip()
    if not ev_id:
        return False
    if bool(allowed.get(ev_id, "")):
        return True
    return bool(_resolve_allowed_id(allowed, ev_id=ev_id))


def _allowed_excerpt(allowed_text: str) -> str:
    s = _norm_ws(str(allowed_text or ""))
    m = _EVID_META_RE.match(s)
    if not m:
        return s
    return _norm_ws(s[m.end() :])


def _ensure_evidence_quote(ev: Dict[str, Any], *, allowed: Dict[str, str], max_chars: int = 180) -> None:
    if not isinstance(ev, dict):
        return
    eid_raw = str(ev.get("id", "") or "").strip()
    eid = _resolve_allowed_id(allowed, ev_id=eid_raw) or eid_raw
    if not eid:
        return
    if eid != eid_raw:
        ev["id"] = eid
    src = allowed.get(eid, "")
    if not src:
        return

    quote = str(ev.get("quote", "") or "").strip()
    if quote:
        # Keep quote only if it's a true substring (after whitespace normalization).
        if _norm_ws(quote) in _norm_ws(src):
            return

    # Fallback: use an excerpt from the provided exemplar text.
    # IMPORTANT: keep it an exact substring (no ellipsis), so evidence is verifiable.
    ex = _allowed_excerpt(src)
    if len(ex) > int(max_chars):
        ex = ex[: int(max_chars)].rstrip()
    ev["quote"] = ex.strip()


_EVID_META_RE = re.compile(r"^\[(?P<pdf>.+?)#p(?P<page>\d+)\]\s*")


def _evidence_meta_from_allowed(allowed_text: str) -> Tuple[str, int]:
    s = str(allowed_text or "").strip()
    m = _EVID_META_RE.match(s)
    if not m:
        return "", 0
    pdf = (m.group("pdf") or "").strip()
    try:
        page = int(m.group("page") or "0")
    except Exception:
        page = 0
    return pdf, max(0, page)


def _attach_evidence_meta(obj: Any, *, allowed: Dict[str, str]):
    if not isinstance(obj, dict):
        return

    def patch_evs(evs: Any):
        if not isinstance(evs, list):
            return
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            eid_raw = str(ev.get("id", "") or "").strip()
            eid = _resolve_allowed_id(allowed, ev_id=eid_raw) or eid_raw
            if not eid:
                continue
            if eid != eid_raw:
                ev["id"] = eid
            if "pdf" in ev and "page" in ev:
                _ensure_evidence_quote(ev, allowed=allowed)
                continue
            pdf, page = _evidence_meta_from_allowed(allowed.get(eid, ""))
            if pdf:
                ev["pdf"] = pdf
            if page:
                ev["page"] = int(page)
            _ensure_evidence_quote(ev, allowed=allowed)

    for d in obj.get("diagnosis", []) or []:
        if isinstance(d, dict):
            patch_evs(d.get("evidence", None))
    for t in obj.get("templates", []) or []:
        if isinstance(t, dict):
            patch_evs(t.get("evidence", None))
    for it in obj.get("issues", []) or []:
        if isinstance(it, dict):
            patch_evs(it.get("evidence", None))
    for it in obj.get("section_template_hints", []) or []:
        if isinstance(it, dict):
            patch_evs(it.get("evidence", None))


def _trim_excerpt(text: str, *, max_chars: int = 380) -> str:
    s = _norm_ws(text)
    if len(s) <= max_chars:
        return s
    return (s[: max_chars - 1] + "…").strip()


def _call_llm_json(
    *,
    llm: OpenAICompatClient,
    prompt: str,
    budget: LLMBudget,
    max_tokens: int,
    timeout_s: float = 180.0,
) -> Tuple[Optional[dict], dict]:
    prompt = (prompt or "").strip()
    if not prompt:
        return None, {}

    # If we've already detected a non-recoverable auth/validation issue, stop
    # making repeated calls that will deterministically fail.
    try:
        if any(str(w or "").startswith("llm_blocked:") for w in (budget.warnings or [])):
            return None, {"skipped": True, "reason": "llm_blocked"}
    except Exception:
        pass

    # Some gateways (Gemini-style) may truncate JSON when max_tokens is too low
    # because hidden reasoning tokens can count into the cap. Retry once with a
    # larger completion budget and an explicit "JSON only" reminder.
    base = max(4096, int(max_tokens or 0))
    token_budget = [base, max(8192, base)]

    last_meta: dict = {}
    last_content: str = ""

    for attempt in range(2):
        prompt2 = prompt
        if attempt >= 1:
            prompt2 = (
                prompt
                + "\n\nYour last response was not valid JSON. Return ONLY one JSON object matching OUTPUT_SCHEMA. No markdown, no code fences."
            )

        max_tok = int(token_budget[min(attempt, len(token_budget) - 1)])
        approx_pt = approx_tokens("Return STRICT JSON only.") + approx_tokens(prompt2)
        if budget.would_exceed_budget(approx_prompt_tokens=approx_pt, max_completion_tokens=max_tok):
            budget.warnings.append("budget_exceeded: skipped LLM call")
            return None, {"skipped": True, "reason": "budget_exceeded"}

        status, resp = llm.chat(
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": prompt2},
            ],
            temperature=0.0,
            max_tokens=max_tok,
            response_format={"type": "json_object"},
            timeout_s=float(timeout_s),
        )

        content = extract_first_content(resp)
        last_content = content or ""
        usage = extract_usage(resp)
        if usage.get("total_tokens", 0) > 0:
            budget.add_usage(usage)
        else:
            budget.add_approx(prompt2, content)

        if int(status or 0) != 200:
            # Surface important API failures to the user via budget warnings.
            try:
                raw_msg = ""
                if isinstance(resp, dict):
                    if isinstance(resp.get("error", None), dict):
                        raw_msg = str((resp.get("error", {}) or {}).get("message", "") or "").strip()
                    if not raw_msg:
                        raw_msg = str(resp.get("_raw", "") or resp.get("_error", "") or "").strip()
                low = raw_msg.lower()
                if int(status or 0) == 403 and ("verify your account" in low or "validation_required" in low):
                    n = 0
                    try:
                        n = int(budget.inc_error("403_validation_required") or 0)
                    except Exception:
                        n = 0
                    if "llm_error:403_validation_required" not in (budget.warnings or []):
                        budget.warnings.append("llm_error:403_validation_required")
                    # Avoid disabling LLM for the whole run on a single transient 403.
                    if n >= 3 and "llm_blocked:403_validation_required" not in (budget.warnings or []):
                        budget.warnings.append("llm_blocked:403_validation_required")
                else:
                    w = f"llm_error:http_{int(status or 0)}"
                    if w not in (budget.warnings or []):
                        budget.warnings.append(w)
            except Exception:
                pass
            last_meta = {"status": int(status or 0), "raw": (content or "")[:600]}
            continue

        # If the provider explicitly reports truncation, retry with a larger budget.
        try:
            fr = ""
            if isinstance(resp, dict):
                choices = resp.get("choices", [])
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    fr = str(choices[0].get("finish_reason", "") or "").strip().lower()
            if fr == "length":
                last_meta = {"status": int(status or 0), "raw": (content or "")[:600], "truncated": True}
                continue
        except Exception:
            pass

        obj = extract_json(content) if isinstance(content, str) else None
        if isinstance(obj, dict):
            return obj, {"status": int(status or 0)}

        last_meta = {"status": int(status or 0), "raw": (content or "")[:600], "parse_error": True}

    if not last_meta:
        last_meta = {"status": 0, "raw": (last_content or "")[:600]}
    return None, last_meta


def review_sentence_alignment(
    *,
    audit_items: List[Dict[str, Any]],
    budget: LLMBudget,
    llm: Optional[OpenAICompatClient],
    coverage: Optional[ReviewCoverageStore] = None,
    top_n: int = 50,
    batch_size: int = 6,
    evidence_top_k: int = 3,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    if llm is None:
        return {"items": [], "skipped": True, "reason": "llm_not_configured"}

    # pick candidates: low_alignment first, then other issues
    def score_key(it: dict) -> Tuple[int, float]:
        issues = it.get("issues", []) or []
        has_low = any((x or {}).get("issue_type") == "low_alignment" for x in issues)
        sc = float(((it.get("alignment") or {}).get("score", 0.0) or 0.0))
        return (0 if has_low else 1, sc)

    cands = list(audit_items or [])
    id_to_key: Dict[int, str] = {}
    id_to_page: Dict[int, int] = {}
    for it in cands:
        if not isinstance(it, dict):
            continue
        try:
            sid = int(it.get("id", -1))
        except Exception:
            sid = -1
        if sid < 0:
            continue
        page = int(it.get("page", 0) or 0)
        txt = str(it.get("text", "") or "").strip()
        key = stable_text_key(prefix="sa", page=page, text=txt)
        id_to_key[sid] = key
        id_to_page[sid] = page
        it["_stable_key"] = key

    def cov_key(it: dict) -> Tuple[int, int, int, float]:
        base = score_key(it)
        page = int(it.get("page", 0) or 0)
        key = str(it.get("_stable_key", "") or "")
        seen = int(coverage.seen_count("sentence_alignment", key)) if (coverage is not None and key) else 0
        page_seen = int(coverage.page_seen_count("sentence_alignment", page)) if (coverage is not None and page) else 0
        return (0 if seen <= 0 else 1, page_seen, int(base[0]), float(base[1]))

    # Prefer unseen targets to avoid repeatedly reviewing the same unchanged text across iterations.
    if coverage is not None:
        unseen = []
        for it in cands:
            key = str(it.get("_stable_key", "") or "")
            if not key:
                continue
            if int(coverage.seen_count("sentence_alignment", key)) <= 0:
                unseen.append(it)
        if unseen:
            cands = unseen
        else:
            return {"items": [], "skipped": True, "reason": "all_seen"}

    cands.sort(key=cov_key)
    # Page-cap to spread attention across the paper (avoid over-focusing on a single page/section).
    max_per_page = 12
    picked = []
    per_page: Dict[int, int] = {}
    for it in cands:
        try:
            page = int(it.get("page", 0) or 0)
        except Exception:
            page = 0
        if page > 0 and max_per_page > 0:
            if int(per_page.get(page, 0) or 0) >= int(max_per_page):
                continue
        picked.append(it)
        if page > 0:
            per_page[page] = int(per_page.get(page, 0) or 0) + 1
        if len(picked) >= max(1, int(top_n)):
            break
    cands = picked

    out_map: Dict[int, Dict[str, Any]] = {}

    total = len(cands)
    if total <= 0:
        return {"items": [], "skipped": True, "reason": "no_candidates"}

    for start in range(0, total, max(1, int(batch_size))):
        batch = cands[start : start + max(1, int(batch_size))]
        allowed: Dict[str, str] = {}
        prompt_lines: List[str] = []
        prompt_lines.append("You are a strict academic writing reviewer. Focus on STYLE alignment to exemplars, not new content.")
        prompt_lines.append("You must be conservative: avoid adding facts, numbers, citations, or new entities.")
        prompt_lines.append("For each target sentence, explain why it sounds unlike the exemplars and propose actionable edits as templates (NOT a full rewrite).")
        prompt_lines.append("Return ONLY JSON. No markdown, no code fences.")
        prompt_lines.append("WHITE-BOX: every point must cite at least one provided exemplar excerpt by id.")
        prompt_lines.append("Evidence quote can be empty, but if non-empty it MUST be an exact substring of that excerpt.")
        prompt_lines.append("")
        prompt_lines.append("OUTPUT_SCHEMA (JSON):")
        prompt_lines.append(
            json.dumps(
                {
                    "items": [
                        {
                            "id": 12,
                            "diagnosis": [
                                {
                                    "problem": "…",
                                    "suggestion": "…",
                                    "evidence": [{"id": "S12_E1", "quote": "exact substring"}],
                                }
                            ],
                            "templates": [
                                {"text": "In this paper, we …", "evidence": [{"id": "S12_E2", "quote": "exact substring"}]}
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        prompt_lines.append("")

        for bi, it in enumerate(batch):
            sid = int(it.get("id", -1) or -1)
            txt = str(it.get("text", "") or "").strip()
            if sid < 0 or not txt:
                continue
            prompt_lines.append(f"T{bi}: id={sid}")
            prompt_lines.append("TEXT: " + _trim_excerpt(txt, max_chars=520))
            exs = ((it.get("alignment") or {}).get("exemplars", []) or [])[: max(1, int(evidence_top_k))]
            for ej, ex in enumerate(exs, start=1):
                ev_id = f"S{sid}_E{ej}"
                ev_txt = _trim_excerpt(str(ex.get("text", "") or ""), max_chars=520)
                meta = f"[{str(ex.get('pdf', '') or '')}#p{int(ex.get('page', 0) or 0)}] "
                allowed[ev_id] = meta + ev_txt
                prompt_lines.append(f"{ev_id}: " + allowed[ev_id])
            prompt_lines.append("")

        prompt = "\n".join(prompt_lines).strip()
        if progress_cb:
            try:
                progress_cb("llm_sentence", min(start, total), total, f"{start+1}-{min(total, start+len(batch))}")
            except Exception:
                pass

        obj, meta = _call_llm_json(llm=llm, prompt=prompt, budget=budget, max_tokens=1500, timeout_s=180.0)
        if obj is None:
            continue
        items = obj.get("items", [])
        if not isinstance(items, list):
            continue

        for x in items:
            if not isinstance(x, dict):
                continue
            try:
                sid = int(x.get("id", -1))
            except Exception:
                continue
            if sid < 0:
                continue

            # Validate evidence ids (quote may be missing; we can attach a verified excerpt later).
            ok_any = False
            for d in x.get("diagnosis", []) or []:
                if not isinstance(d, dict):
                    continue
                for ev in d.get("evidence", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                        ok_any = True
                        break
                if ok_any:
                    break
            if not ok_any:
                for t in x.get("templates", []) or []:
                    if not isinstance(t, dict):
                        continue
                    for ev in t.get("evidence", []) or []:
                        if not isinstance(ev, dict):
                            continue
                        if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                            ok_any = True
                            break
                    if ok_any:
                        break
            if not ok_any:
                continue
            _attach_evidence_meta(x, allowed=allowed)
            if coverage is not None:
                key = id_to_key.get(sid, "")
                page = id_to_page.get(sid, 0)
                if key:
                    x["stable_key"] = key
                    coverage.mark_seen("sentence_alignment", key, page=page, meta={"kind": "sentence", "page": int(page or 0)})
            out_map[sid] = x

    out_items = list(out_map.values())
    out_items.sort(key=lambda d: int(d.get("id", 0) or 0))
    return {"items": out_items, "skipped": False}


def review_outline_structure(
    *,
    paper_headings: List[Dict[str, Any]],
    exemplar_outlines: List[Dict[str, Any]],
    budget: LLMBudget,
    llm: Optional[OpenAICompatClient],
    coverage: Optional[ReviewCoverageStore] = None,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    if llm is None:
        return {"skipped": True, "reason": "llm_not_configured"}
    if not paper_headings:
        return {"skipped": True, "reason": "no_headings"}
    if not exemplar_outlines:
        return {"skipped": True, "reason": "no_exemplar_outlines"}

    outline_key = stable_text_key(prefix="ol", page=0, text=" ".join([str(h.get("text", "") or "") for h in (paper_headings or [])[:80]]))
    if coverage is not None and int(coverage.seen_count("outline", outline_key)) > 0:
        return {"skipped": True, "reason": "all_seen"}

    allowed: Dict[str, str] = {}
    lines: List[str] = []
    lines.append("You review a paper's section outline compared to exemplar outlines.")
    lines.append("Focus on structure: missing/odd sections, ordering, granularity, and transitions between sections.")
    lines.append("Do NOT rewrite content. Output actionable suggestions as checklists and section-template hints.")
    lines.append("Return ONLY JSON. No markdown, no code fences.")
    lines.append("WHITE-BOX: cite exemplars by id.")
    lines.append("Evidence quote can be empty, but if non-empty it MUST be an exact substring of the provided outline text.")
    lines.append("")
    lines.append("OUTPUT_SCHEMA (JSON):")
    lines.append(
        json.dumps(
            {
                "summary": "…",
                "issues": [
                    {
                        "problem": "missing_methods_section",
                        "detail": "…",
                        "suggestion": "…",
                        "evidence": [{"id": "O1", "quote": "exact substring"}],
                    }
                ],
                "section_template_hints": [{"canonical": "methods", "hint": "…", "evidence": [{"id": "O2", "quote": "exact substring"}]}],
            },
            ensure_ascii=False,
        )
    )
    lines.append("")

    lines.append("PAPER_OUTLINE:")
    for h in paper_headings[:80]:
        page = int(h.get("page", 0) or 0)
        level = int(h.get("level", 1) or 1)
        t = _norm_ws(str(h.get("text", "") or ""))
        if not t:
            continue
        lines.append(f"- (p{page}) L{level} {t}")
    lines.append("")

    lines.append("EXEMPLAR_OUTLINES:")
    for i, o in enumerate(exemplar_outlines[:6], start=1):
        seq = _norm_ws(str(o.get("seq", "") or ""))
        ex = o.get("example", {}) if isinstance(o.get("example", {}), dict) else {}
        pdf_rel = _norm_ws(str(ex.get("pdf_rel", "") or ""))
        oid = f"O{i}"
        allowed[oid] = f"[{pdf_rel}#p0] {seq}"
        lines.append(f"{oid}: {allowed[oid]}")
    lines.append("")

    if progress_cb:
        try:
            progress_cb("llm_outline", 0, 1, "outline")
        except Exception:
            pass

    prompt = "\n".join(lines).strip()
    obj, meta = _call_llm_json(llm=llm, prompt=prompt, budget=budget, max_tokens=900, timeout_s=180.0)
    if not isinstance(obj, dict):
        return {"skipped": True, "reason": "llm_failed"}

    # Validate at least one evidence id exists (quote may be missing; we will attach a verified excerpt).
    ok_any = False
    for it in obj.get("issues", []) or []:
        if not isinstance(it, dict):
            continue
        for ev in it.get("evidence", []) or []:
            if not isinstance(ev, dict):
                continue
            if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                ok_any = True
                break
        if ok_any:
            break
    if not ok_any:
        return {"skipped": True, "reason": "invalid_evidence"}
    _attach_evidence_meta(obj, allowed=allowed)
    obj["skipped"] = False
    if coverage is not None:
        coverage.mark_seen("outline", outline_key, page=0, meta={"kind": "outline"})
    return obj


def review_citation_style(
    *,
    paper_citation_sentences: List[Dict[str, Any]],
    cite_search: Callable[[str, int], List[Tuple[float, Dict[str, Any]]]],
    budget: LLMBudget,
    llm: Optional[OpenAICompatClient],
    coverage: Optional[ReviewCoverageStore] = None,
    top_n: int = 30,
    batch_size: int = 6,
    evidence_top_k: int = 3,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    if llm is None:
        return {"items": [], "skipped": True, "reason": "llm_not_configured"}
    if not paper_citation_sentences:
        return {"items": [], "skipped": True, "reason": "no_citations"}

    # Pick richer citation sentences first (more cited items, longer).
    def key(it: dict) -> Tuple[int, int]:
        cits = it.get("citations", [])
        try:
            n = int(len(cits) if isinstance(cits, list) else 0)
        except Exception:
            n = 0
        return (-n, -len(str(it.get("sentence", "") or "")))

    cands = list(paper_citation_sentences or [])
    for it in cands:
        if not isinstance(it, dict):
            continue
        sent_raw = str(it.get("sentence", "") or "").strip()
        page = int(it.get("page", 0) or 0)
        it["_stable_key"] = stable_text_key(prefix="cs", page=page, text=sent_raw)

    def cov_key(it: dict) -> Tuple[int, int, int, int]:
        page = int(it.get("page", 0) or 0)
        sk = str(it.get("_stable_key", "") or "")
        seen = int(coverage.seen_count("citation_style", sk)) if (coverage is not None and sk) else 0
        page_seen = int(coverage.page_seen_count("citation_style", page)) if (coverage is not None and page) else 0
        base = key(it)
        return (0 if seen <= 0 else 1, page_seen, int(base[0]), int(base[1]))

    if coverage is not None:
        unseen = []
        for it in cands:
            sk = str(it.get("_stable_key", "") or "")
            if not sk:
                continue
            if int(coverage.seen_count("citation_style", sk)) <= 0:
                unseen.append(it)
        if unseen:
            cands = unseen
        else:
            return {"items": [], "skipped": True, "reason": "all_seen"}

    cands.sort(key=cov_key)
    max_per_page = 8
    picked = []
    per_page: Dict[int, int] = {}
    for it in cands:
        page = int(it.get("page", 0) or 0)
        if page > 0 and max_per_page > 0:
            if int(per_page.get(page, 0) or 0) >= int(max_per_page):
                continue
        picked.append(it)
        if page > 0:
            per_page[page] = int(per_page.get(page, 0) or 0) + 1
        if len(picked) >= max(1, int(top_n)):
            break
    cands = picked
    total = len(cands)

    out_items: List[Dict[str, Any]] = []
    out_seen: set[str] = set()

    for start in range(0, total, max(1, int(batch_size))):
        batch = cands[start : start + max(1, int(batch_size))]
        allowed: Dict[str, str] = {}
        lines: List[str] = []
        lines.append("You review in-text citation sentence style compared to exemplar citation sentences.")
        lines.append("Goal: make the sentence sound like top papers while keeping the same meaning (no new claims).")
        lines.append("Do NOT produce a full rewrite; only output diagnosis + template snippets.")
        lines.append("Return ONLY JSON. No markdown, no code fences.")
        lines.append("WHITE-BOX: cite exemplar sentences by id.")
        lines.append("Evidence quote can be empty, but if non-empty it MUST be an exact substring of provided exemplar text.")
        lines.append("")
        lines.append("OUTPUT_SCHEMA (JSON):")
        lines.append(
            json.dumps(
                {
                    "items": [
                        {
                            "page": 3,
                            "sentence": "…",
                            "diagnosis": [{"problem": "…", "suggestion": "…", "evidence": [{"id": "CSd34db33f_E1", "quote": "…"}]}],
                            "templates": [{"text": "Consistent with …, we …", "evidence": [{"id": "CSd34db33f_E2", "quote": "…"}]}],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        lines.append("")

        for bi, it in enumerate(batch):
            sent_raw = str(it.get("sentence", "") or "").strip()
            sent = _trim_excerpt(sent_raw, max_chars=520)
            if not sent:
                continue
            sid = hashlib.sha1(sent_raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
            page = int(it.get("page", 0) or 0)
            lines.append(f"P{bi}: page={page}")
            lines.append("SENTENCE: " + sent)
            exs = cite_search(sent, max(1, int(evidence_top_k))) or []
            for ej, (_sc, ex) in enumerate(exs[: max(1, int(evidence_top_k))], start=1):
                ev_id = f"CS{sid}_E{ej}"
                ev_txt = _trim_excerpt(str(ex.get("sentence", "") or ex.get("text", "") or ""), max_chars=520)
                meta = f"[{str(ex.get('pdf', '') or ex.get('pdf_rel', '') or '')}#p{int(ex.get('page', 0) or 0)}] "
                allowed[ev_id] = meta + ev_txt
                lines.append(f"{ev_id}: " + allowed[ev_id])
            lines.append("")

        prompt = "\n".join(lines).strip()
        if progress_cb:
            try:
                progress_cb("llm_cite", min(start, total), total, f"{start+1}-{min(total, start+len(batch))}")
            except Exception:
                pass

        obj, meta = _call_llm_json(llm=llm, prompt=prompt, budget=budget, max_tokens=1200, timeout_s=180.0)
        if not isinstance(obj, dict):
            continue
        items = obj.get("items", [])
        if not isinstance(items, list):
            continue

        for x in items:
            if not isinstance(x, dict):
                continue
            sent = _norm_ws(str(x.get("sentence", "") or ""))
            if not sent or sent in out_seen:
                continue

            ok_any = False
            for d in x.get("diagnosis", []) or []:
                if not isinstance(d, dict):
                    continue
                for ev in d.get("evidence", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                        ok_any = True
                        break
                if ok_any:
                    break
            if not ok_any:
                for t in x.get("templates", []) or []:
                    if not isinstance(t, dict):
                        continue
                    for ev in t.get("evidence", []) or []:
                        if not isinstance(ev, dict):
                            continue
                        if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                            ok_any = True
                            break
                    if ok_any:
                        break
            if not ok_any:
                continue
            _attach_evidence_meta(x, allowed=allowed)
            if coverage is not None:
                page = int(x.get("page", 0) or 0)
                sent_raw = str(x.get("sentence", "") or "").strip()
                sk = stable_text_key(prefix="cs", page=page, text=sent_raw)
                x["stable_key"] = sk
                coverage.mark_seen("citation_style", sk, page=page, meta={"kind": "citation_style", "page": int(page or 0)})
            out_seen.add(sent)
            out_items.append(x)

    return {"items": out_items, "skipped": False}


def review_paragraph_alignment(
    *,
    paper_paragraphs: List[Dict[str, Any]],
    rag_search: Callable[[str, int], List[Tuple[float, Dict[str, Any]]]],
    budget: LLMBudget,
    llm: Optional[OpenAICompatClient],
    coverage: Optional[ReviewCoverageStore] = None,
    top_n: int = 16,
    batch_size: int = 3,
    evidence_top_k: int = 3,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    if llm is None:
        return {"items": [], "skipped": True, "reason": "llm_not_configured"}
    if not paper_paragraphs:
        return {"items": [], "skipped": True, "reason": "no_paragraphs"}

    # Pick paragraphs that are likely problematic (too long / too dense / early sections).
    scored: List[Tuple[int, int, int, float, Dict[str, Any]]] = []
    pid_to_key: Dict[str, str] = {}
    for p in paper_paragraphs:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", "") or "").strip()
        txt = str(p.get("text", "") or "").strip()
        if not pid or len(txt) < 80:
            continue
        page = int(p.get("page", 0) or 0)
        lang = str(p.get("lang", "") or "").strip().lower() or "en"
        en_words = len(re.findall(r"\b[a-z]+\b", txt.lower()))
        zh_chars = len(re.findall(r"[\u4e00-\u9fff]", txt))
        sent_count = 0
        try:
            sent_count = int(len(p.get("sentences", []) or []))
        except Exception:
            sent_count = 0

        long_flag = (en_words >= 120) or (zh_chars >= 280) or (sent_count >= 6)
        if not long_flag and page > 2 and sent_count <= 3:
            # Keep some intro paragraphs even if not long.
            continue

        score = 0.0
        if page <= 2:
            score += 1.0
        if en_words >= 160 or zh_chars >= 380:
            score += 2.0
        elif en_words >= 120 or zh_chars >= 280:
            score += 1.0
        score += min(2.0, float(sent_count) / 4.0)
        score += min(2.0, float(len(txt)) / 900.0)

        sk = stable_text_key(prefix="pa", page=page, text=txt)
        pid_to_key[pid] = sk
        seen = int(coverage.seen_count("paragraph_alignment", sk)) if (coverage is not None and sk) else 0
        page_seen = int(coverage.page_seen_count("paragraph_alignment", page)) if (coverage is not None and page) else 0
        scored.append((0 if seen <= 0 else 1, page_seen, seen, score, p))

    if coverage is not None:
        scored_unseen = [x for x in scored if int(x[0]) == 0]
        if scored_unseen:
            scored = scored_unseen
        else:
            return {"items": [], "skipped": True, "reason": "all_seen"}

    scored.sort(key=lambda kv: (int(kv[0]), int(kv[1]), -float(kv[3]), int((kv[4] or {}).get("page", 0) or 0), str((kv[4] or {}).get("id", ""))))
    # Page-cap to avoid concentrating review on the same early pages.
    max_per_page = 3
    picked = []
    per_page: Dict[int, int] = {}
    for _a, _b, _c, _s, p in scored:
        page = int((p or {}).get("page", 0) or 0)
        if page > 0 and max_per_page > 0:
            if int(per_page.get(page, 0) or 0) >= int(max_per_page):
                continue
        picked.append(p)
        if page > 0:
            per_page[page] = int(per_page.get(page, 0) or 0) + 1
        if len(picked) >= max(1, int(top_n)):
            break
    cands = picked
    total = len(cands)
    if total <= 0:
        return {"items": [], "skipped": True, "reason": "no_candidates"}

    out_map: Dict[str, Dict[str, Any]] = {}

    for start in range(0, total, max(1, int(batch_size))):
        batch = cands[start : start + max(1, int(batch_size))]
        allowed: Dict[str, str] = {}
        lines: List[str] = []
        lines.append("You are a strict academic writing reviewer. Focus on PARAGRAPH structure and academic tone.")
        lines.append("Do NOT rewrite the paragraph. Only provide diagnosis + reusable templates/snippets.")
        lines.append("Do NOT add new facts, numbers, citations, or entities.")
        lines.append("Return ONLY JSON. No markdown, no code fences.")
        lines.append("WHITE-BOX: every point must cite exemplars by id.")
        lines.append("Evidence quote can be empty, but if non-empty it MUST be an exact substring of the provided exemplar excerpt.")
        lines.append("")
        lines.append("OUTPUT_SCHEMA (JSON):")
        lines.append(
            json.dumps(
                {
                    "items": [
                        {
                            "paragraph_id": "P0",
                            "page": 1,
                            "diagnosis": [
                                {
                                    "problem": "…",
                                    "suggestion": "…",
                                    "evidence": [{"id": "P0_E1", "quote": "exact substring"}],
                                }
                            ],
                            "templates": [
                                {"text": "To this end, we …", "evidence": [{"id": "P0_E2", "quote": "exact substring"}]}
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        lines.append("")

        for p in batch:
            pid = str(p.get("id", "") or "").strip()
            page = int(p.get("page", 0) or 0)
            txt = str(p.get("text", "") or "").strip()
            if not pid or not txt:
                continue
            lines.append(f"PARAGRAPH {pid} (p{page}):")
            lines.append("TEXT: " + _trim_excerpt(txt, max_chars=1100))
            exs = rag_search(txt, max(1, int(evidence_top_k))) or []
            for ej, (_sc, ex) in enumerate(exs[: max(1, int(evidence_top_k))], start=1):
                ev_id = f"{pid}_E{ej}"
                ev_txt = _trim_excerpt(str(ex.get("text", "") or ""), max_chars=520)
                meta = f"[{str(ex.get('pdf', '') or '')}#p{int(ex.get('page', 0) or 0)}] "
                allowed[ev_id] = meta + ev_txt
                lines.append(f"{ev_id}: " + allowed[ev_id])
            lines.append("")

        prompt = "\n".join(lines).strip()
        if progress_cb:
            try:
                progress_cb("llm_paragraph", min(start, total), total, f"{start+1}-{min(total, start+len(batch))}")
            except Exception:
                pass

        obj, meta = _call_llm_json(llm=llm, prompt=prompt, budget=budget, max_tokens=1400, timeout_s=180.0)
        if not isinstance(obj, dict):
            continue
        items = obj.get("items", [])
        if not isinstance(items, list):
            continue

        for x in items:
            if not isinstance(x, dict):
                continue
            pid = str(x.get("paragraph_id", "") or "").strip()
            if not pid:
                continue
            # Validate at least one evidence id (quote may be missing; we can attach a verified excerpt later).
            ok_any = False
            for d in x.get("diagnosis", []) or []:
                if not isinstance(d, dict):
                    continue
                for ev in d.get("evidence", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                        ok_any = True
                        break
                if ok_any:
                    break
            if not ok_any:
                for t in x.get("templates", []) or []:
                    if not isinstance(t, dict):
                        continue
                    for ev in t.get("evidence", []) or []:
                        if not isinstance(ev, dict):
                            continue
                        if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                            ok_any = True
                            break
                    if ok_any:
                        break
            if not ok_any:
                continue
            _attach_evidence_meta(x, allowed=allowed)
            if coverage is not None:
                key = pid_to_key.get(pid, "")
                page = int(x.get("page", 0) or 0)
                if key:
                    x["stable_key"] = key
                    coverage.mark_seen("paragraph_alignment", key, page=page, meta={"kind": "paragraph", "page": int(page or 0)})
            out_map[pid] = x

    out_items = list(out_map.values())
    out_items.sort(key=lambda d: str(d.get("paragraph_id", "")))
    return {"items": out_items, "skipped": False}


def _token_in_text(token: str, text: str, *, language: str) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    s = str(text or "")
    lang = (language or "").strip().lower() or "en"
    if lang == "zh":
        return token in s
    # English (case-insensitive word-boundary match).
    try:
        return re.search(rf"(?i)(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", s) is not None
    except Exception:
        return token.lower() in s.lower()


def _find_token_context(paper_structure: Dict[str, Any], *, token: str, language: str) -> Dict[str, Any]:
    lang = (language or "").strip().lower() or "en"
    paras = paper_structure.get("paragraphs", []) if isinstance(paper_structure, dict) else []
    if not isinstance(paras, list):
        paras = []

    for p in paras:
        if not isinstance(p, dict):
            continue
        page = int(p.get("page", 0) or 0)
        pid = str(p.get("id", "") or "").strip()

        sents = p.get("sentences", [])
        if isinstance(sents, list) and sents:
            for s in sents:
                if not isinstance(s, dict):
                    continue
                st = str(s.get("text", "") or "").strip()
                if not st:
                    continue
                if _token_in_text(token, st, language=lang):
                    return {"page": page, "paragraph_id": pid, "sentence": st}

        txt = str(p.get("text", "") or "").strip()
        if txt and _token_in_text(token, txt, language=lang):
            return {"page": page, "paragraph_id": pid, "sentence": _trim_excerpt(txt, max_chars=520)}

    # Fallback: scan headings (sometimes tokens appear in section titles).
    heads = paper_structure.get("headings", []) if isinstance(paper_structure, dict) else []
    if isinstance(heads, list):
        for h in heads:
            if not isinstance(h, dict):
                continue
            page = int(h.get("page", 0) or 0)
            t = str(h.get("text", "") or "").strip()
            if t and _token_in_text(token, t, language=lang):
                return {"page": page, "paragraph_id": "", "sentence": t}

    return {"page": 0, "paragraph_id": "", "sentence": ""}


def review_lexical_alignment(
    *,
    lexical: Dict[str, Any],
    paper_structure: Dict[str, Any],
    rag_search: Callable[[str, int], List[Tuple[float, Dict[str, Any]]]],
    budget: LLMBudget,
    llm: Optional[OpenAICompatClient],
    coverage: Optional[ReviewCoverageStore] = None,
    top_n: int = 40,
    batch_size: int = 6,
    evidence_top_k: int = 2,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    """
    Token-level "rare in exemplars" review (programmatic detection + LLM explanation).
    """

    if llm is None:
        return {"items": [], "skipped": True, "reason": "llm_not_configured"}
    if not isinstance(lexical, dict):
        return {"items": [], "skipped": True, "reason": "no_lexical_stats"}
    rare = lexical.get("rare_in_exemplars", {}) if isinstance(lexical.get("rare_in_exemplars", {}), dict) else {}
    if not rare:
        return {"items": [], "skipped": True, "reason": "no_rare_tokens"}

    cands: List[Dict[str, Any]] = []
    for lang in ("en", "zh"):
        items = rare.get(lang, [])
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            token = str(it.get("token", "") or "").strip()
            if not token:
                continue
            try:
                pc = int(it.get("paper_count", 0) or 0)
            except Exception:
                pc = 0
            if pc <= 0:
                continue
            try:
                df = int(it.get("exemplar_doc_freq", 0) or 0)
            except Exception:
                df = 0
            try:
                ratio = float(it.get("exemplar_doc_ratio", 0.0) or 0.0)
            except Exception:
                ratio = 0.0
            sk = stable_text_key(prefix="lx", page=0, text=token, extra=str(lang))
            cands.append(
                {
                    "token": token,
                    "language": lang,
                    "paper_count": pc,
                    "exemplar_doc_freq": df,
                    "exemplar_doc_ratio": ratio,
                    "_stable_key": sk,
                }
            )

    if not cands:
        return {"items": [], "skipped": True, "reason": "no_candidates"}

    # Prefer unseen tokens to make repeated runs converge.
    if coverage is not None:
        unseen = [x for x in cands if int(coverage.seen_count("lexical", str(x.get("_stable_key", "") or ""))) <= 0]
        if unseen:
            cands = unseen
        else:
            return {"items": [], "skipped": True, "reason": "all_seen"}

    cands.sort(key=lambda d: (-int(d.get("paper_count", 0) or 0), int(d.get("exemplar_doc_freq", 0) or 0), float(d.get("exemplar_doc_ratio", 0.0) or 0.0)))
    cands = cands[: max(1, int(top_n))]
    total = len(cands)
    if total <= 0:
        return {"items": [], "skipped": True, "reason": "no_candidates"}

    out_items: List[Dict[str, Any]] = []
    out_seen: set[str] = set()

    for start in range(0, total, max(1, int(batch_size))):
        batch = cands[start : start + max(1, int(batch_size))]
        allowed: Dict[str, str] = {}
        lines: List[str] = []

        lines.append("You review WORD CHOICE issues based on a top-paper exemplar corpus.")
        lines.append("Input includes tokens that are frequent in the user's paper but rare in the exemplar corpus.")
        lines.append("Your goal is to explain whether the token should be kept (domain term/variable) or replaced, and how to rewrite conservatively.")
        lines.append("Do NOT add new facts, numbers, citations, or entities.")
        lines.append("Return ONLY JSON. No markdown, no code fences.")
        lines.append("WHITE-BOX: cite exemplar excerpts by id. Evidence quote can be empty, but if non-empty it MUST be an exact substring of the provided exemplar excerpt.")
        lines.append("")
        lines.append("OUTPUT_SCHEMA (JSON):")
        lines.append(
            json.dumps(
                {
                    "items": [
                        {
                            "token": "oracle",
                            "language": "en|zh",
                            "page": 12,
                            "paper_count": 62,
                            "exemplar_doc_freq": 2,
                            "exemplar_doc_ratio": 0.008,
                            "context_sentence": "…",
                            "diagnosis": [{"problem": "…", "suggestion": "…", "evidence": [{"id": "T0_E1", "quote": "…"}]}],
                            "templates": [{"text": "…", "evidence": [{"id": "T0_E2", "quote": "…"}]}],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        lines.append("")

        included = []
        for bi, it in enumerate(batch):
            tok = str(it.get("token", "") or "").strip()
            lang = str(it.get("language", "") or "en").strip().lower() or "en"
            pc = int(it.get("paper_count", 0) or 0)
            df = int(it.get("exemplar_doc_freq", 0) or 0)
            ratio = float(it.get("exemplar_doc_ratio", 0.0) or 0.0)
            ctx = _find_token_context(paper_structure, token=tok, language=lang)
            page = int(ctx.get("page", 0) or 0)
            sent = _trim_excerpt(str(ctx.get("sentence", "") or ""), max_chars=520)

            if not sent:
                continue

            exs = []
            try:
                exs = rag_search(sent, max(1, int(evidence_top_k))) or []
            except Exception:
                exs = []
            if not exs:
                continue

            ix = len(included)
            included.append({"tok": tok, "lang": lang, "pc": pc, "df": df, "ratio": ratio, "page": page, "sent": sent})
            lines.append(f"T{ix}: token={tok} lang={lang} page={page} paper_count={pc} exemplar_doc_freq={df} exemplar_doc_ratio={ratio:.4f}")
            lines.append("CONTEXT: " + sent)

            # Provide a small set of exemplar excerpts to constrain suggestions.
            for ej, (_sc, ex) in enumerate(exs[: max(1, int(evidence_top_k))], start=1):
                ev_id = f"T{ix}_E{ej}"
                ev_txt = _trim_excerpt(str(ex.get("text", "") or ""), max_chars=380)
                meta = f"[{str(ex.get('pdf', '') or '')}#p{int(ex.get('page', 0) or 0)}] "
                allowed[ev_id] = meta + ev_txt
                lines.append(f"{ev_id}: " + allowed[ev_id])

            lines.append("")

        if not included:
            continue

        prompt = "\n".join(lines).strip()
        if progress_cb:
            try:
                progress_cb("llm_lexical", min(start, total), total, f"{start+1}-{min(total, start+len(batch))}")
            except Exception:
                pass

        obj, meta = _call_llm_json(llm=llm, prompt=prompt, budget=budget, max_tokens=2200, timeout_s=180.0)
        if not isinstance(obj, dict):
            continue
        items = obj.get("items", [])
        if not isinstance(items, list):
            continue

        for x in items:
            if not isinstance(x, dict):
                continue
            tok = _norm_ws(str(x.get("token", "") or ""))
            if not tok or tok in out_seen:
                continue

            ok_any = False
            for d in x.get("diagnosis", []) or []:
                if not isinstance(d, dict):
                    continue
                for ev in d.get("evidence", []) or []:
                    if not isinstance(ev, dict):
                        continue
                    if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                        ok_any = True
                        break
                if ok_any:
                    break
            if not ok_any:
                for t in x.get("templates", []) or []:
                    if not isinstance(t, dict):
                        continue
                    for ev in t.get("evidence", []) or []:
                        if not isinstance(ev, dict):
                            continue
                        if _evidence_id_ok(allowed, ev_id=str(ev.get("id", "") or "")):
                            ok_any = True
                            break
                    if ok_any:
                        break
            if not ok_any:
                continue

            _attach_evidence_meta(x, allowed=allowed)

            # Attach stable_key + coverage
            lang = str(x.get("language", "") or "").strip().lower() or "en"
            sk = stable_text_key(prefix="lx", page=0, text=tok, extra=str(lang))
            x["stable_key"] = sk
            if coverage is not None:
                try:
                    page = int(x.get("page", 0) or 0)
                except Exception:
                    page = 0
                coverage.mark_seen("lexical", sk, page=page, meta={"kind": "lexical", "token": tok, "lang": lang})

            out_seen.add(tok)
            out_items.append(x)

    return {"items": out_items, "skipped": False}


def run_llm_audit_pack(
    *,
    audit_result: Dict[str, Any],
    paper_structure: Dict[str, Any],
    exemplar_outlines: List[Dict[str, Any]],
    rag_search: Optional[Callable[[str, int], List[Tuple[float, Dict[str, Any]]]]] = None,
    cite_search: Optional[Callable[[str, int], List[Tuple[float, Dict[str, Any]]]]] = None,
    llm: Optional[OpenAICompatClient],
    budget: Optional[LLMBudget] = None,
    coverage: Optional[ReviewCoverageStore] = None,
    max_total_tokens: int = 0,
    cost_per_1m_tokens: float = 0.0,
    max_cost: float = 0.0,
    # Deprecated aliases (kept for compatibility):
    cost_per_1m_tokens_rmb: Optional[float] = None,
    max_cost_rmb: Optional[float] = None,
    progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
) -> Dict[str, Any]:
    if cost_per_1m_tokens_rmb is not None:
        cost_per_1m_tokens = float(cost_per_1m_tokens_rmb or 0.0)
    if max_cost_rmb is not None:
        max_cost = float(max_cost_rmb or 0.0)
    if budget is None:
        budget = LLMBudget(
            max_total_tokens=int(max_total_tokens or 0),
            cost_per_1m_tokens=float(cost_per_1m_tokens),
            max_cost=float(max_cost),
        )

    reviews: Dict[str, Any] = {}

    items = audit_result.get("items", []) if isinstance(audit_result, dict) else []
    if isinstance(items, list):
        reviews["sentence_alignment"] = review_sentence_alignment(
            audit_items=items,
            budget=budget,
            llm=llm,
            coverage=coverage,
            top_n=500,
            batch_size=10,
            evidence_top_k=3,
            progress_cb=progress_cb,
        )

    paras = paper_structure.get("paragraphs", []) if isinstance(paper_structure, dict) else []
    if callable(rag_search) and isinstance(paras, list):
        reviews["paragraph_alignment"] = review_paragraph_alignment(
            paper_paragraphs=paras,
            rag_search=rag_search,
            budget=budget,
            llm=llm,
            coverage=coverage,
            top_n=160,
            batch_size=5,
            evidence_top_k=3,
            progress_cb=progress_cb,
        )

    heads = paper_structure.get("headings", []) if isinstance(paper_structure, dict) else []
    if isinstance(heads, list):
        reviews["outline"] = review_outline_structure(
            paper_headings=heads,
            exemplar_outlines=exemplar_outlines or [],
            budget=budget,
            llm=llm,
            coverage=coverage,
            progress_cb=progress_cb,
        )

    cits = paper_structure.get("citations", []) if isinstance(paper_structure, dict) else []
    if callable(cite_search) and isinstance(cits, list):
        reviews["citation_style"] = review_citation_style(
            paper_citation_sentences=cits,
            cite_search=cite_search,
            budget=budget,
            llm=llm,
            coverage=coverage,
            top_n=240,
            batch_size=10,
            evidence_top_k=3,
            progress_cb=progress_cb,
        )

    # Lexical review (rare-in-exemplars token list -> LLM explanation)
    lex = audit_result.get("lexical", {}) if isinstance(audit_result, dict) else {}
    if callable(rag_search) and isinstance(lex, dict) and isinstance(paper_structure, dict):
        reviews["lexical"] = review_lexical_alignment(
            lexical=lex,
            paper_structure=paper_structure,
            rag_search=rag_search,
            budget=budget,
            llm=llm,
            coverage=coverage,
            top_n=60,
            batch_size=4,
            evidence_top_k=1,
            progress_cb=progress_cb,
        )

    return {
        "reviews": reviews,
        "usage": {
            "calls": int(budget.calls),
            "prompt_tokens": int(budget.prompt_tokens),
            "completion_tokens": int(budget.completion_tokens),
            "total_tokens": int(budget.total_tokens),
            "approx_total_tokens": int(budget.approx_total_tokens),
            "max_total_tokens": int(getattr(budget, "max_total_tokens", 0) or 0),
            "remaining_tokens": int(getattr(budget, "budget_remaining_tokens", lambda: 0)() or 0),
            # Optional estimate only (unitless; depends on user-provided rate).
            "cost_per_1m_tokens": float(getattr(budget, "cost_per_1m_tokens", 0.0) or 0.0),
            "estimated_cost": float(getattr(budget, "estimated_cost", lambda: 0.0)() or 0.0),
            "max_cost": float(getattr(budget, "max_cost", 0.0) or 0.0),
            "warnings": list(budget.warnings),
        },
    }
