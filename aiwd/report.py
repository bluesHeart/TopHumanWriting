# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional


def _dt_str(ts: Any) -> str:
    try:
        t = int(ts or 0)
    except Exception:
        t = 0
    if t <= 0:
        return "—"
    try:
        return _dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(t)


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def _int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _md_escape(s: str) -> str:
    # Minimal escape for Markdown; keep it readable.
    return (s or "").replace("\r", "").replace("\n", " ").strip()


def _render_evidence(evs: Any, *, max_items: int = 2) -> str:
    if not isinstance(evs, list) or not evs:
        return ""
    parts: List[str] = []
    for ev in evs[: max(1, int(max_items))]:
        if not isinstance(ev, dict):
            continue
        pdf = str(ev.get("pdf", "") or "").strip()
        page = _int(ev.get("page", 0), 0)
        quote = str(ev.get("quote", "") or "").strip()
        if not quote:
            continue
        loc = f"{pdf}#p{page}" if pdf and page else (pdf or "")
        if loc:
            parts.append(f"- 证据：`{_md_escape(loc)}` · “{_md_escape(quote)}”")
        else:
            parts.append(f"- 证据：“{_md_escape(quote)}”")
    return "\n".join(parts)


def audit_to_markdown(result: Dict[str, Any]) -> str:
    r = result if isinstance(result, dict) else {}
    meta = r.get("meta", {}) if isinstance(r.get("meta", {}), dict) else {}
    summary = r.get("summary", {}) if isinstance(r.get("summary", {}), dict) else {}
    lexical = r.get("lexical", {}) if isinstance(r.get("lexical", {}), dict) else {}
    llm_usage = r.get("llm_usage", {}) if isinstance(r.get("llm_usage", {}), dict) else {}
    llm_reviews = r.get("llm_reviews", {}) if isinstance(r.get("llm_reviews", {}), dict) else {}

    lines: List[str] = []
    lines.append("# TopHumanWriting · 全稿体检报告")
    lines.append("")
    lines.append("## 概览")
    lines.append(f"- 生成时间：{_dt_str(meta.get('created_at', 0))}")
    lines.append(f"- 语言：{_md_escape(str(meta.get('language', '') or '—'))}")
    lines.append(f"- 页数：{_int(meta.get('paper_pages', 0), 0)}")
    lines.append(f"- 句子总数：{_int(summary.get('sentence_total', 0), 0)}")
    lines.append(f"- 已做对齐检索：{_int(summary.get('sentence_scored', 0), 0)}")
    lines.append(f"- 对齐度低句子：{_int(summary.get('low_alignment_sentences', 0), 0)}")
    if meta.get("truncated"):
        lines.append("- 注意：对齐检索已抽样；可调大 max_sentences 获得更全覆盖。")

    if llm_usage and _int(llm_usage.get("calls", 0), 0) > 0:
        lines.append("")
        lines.append("## LLM 使用")
        tokens = _int(llm_usage.get("total_tokens", 0), 0) + _int(llm_usage.get("approx_total_tokens", 0), 0)
        max_tokens = _int(llm_usage.get("max_total_tokens", 0), 0)
        remaining_tokens = _int(llm_usage.get("remaining_tokens", 0), 0)
        lines.append(f"- 调用次数：{_int(llm_usage.get('calls', 0), 0)}")
        lines.append(f"- tokens≈{tokens}")
        if max_tokens > 0:
            lines.append(f"- token 预算：{max_tokens}（剩余≈{remaining_tokens}）")

        # Optional cost estimate (unitless; depends on user-provided rate).
        cost_per = _num(llm_usage.get("cost_per_1m_tokens", llm_usage.get("cost_per_1m_tokens_rmb", 0.0)), 0.0)
        budget = _num(llm_usage.get("max_cost", llm_usage.get("max_cost_rmb", 0.0)), 0.0)
        if cost_per > 0 and budget > 0:
            cost = float(tokens) * float(cost_per) / 1_000_000.0
            lines.append(f"- 估算成本≈{cost:.4f} / 预算 {budget:g}（单位取决于你设置的单价）")
        warns = llm_usage.get("warnings", [])
        if isinstance(warns, list):
            ww = [str(x) for x in warns if str(x)]
        else:
            ww = []
        if ww:
            lines.append("- 警告：")
            for w in ww[:8]:
                w2 = str(w or "").strip()
                if w2 == "llm_blocked:403_validation_required":
                    lines.append("  - LLM 接口 403：需要账号验证/权限（请更换 Key/Model 或完成验证）")
                    continue
                if w2.startswith("llm_error:http_"):
                    lines.append(f"  - LLM 接口错误：{_md_escape(w2)}")
                    continue
                lines.append(f"  - {_md_escape(w2)}")

    # Outline review
    outline = llm_reviews.get("outline", {}) if isinstance(llm_reviews.get("outline", {}), dict) else {}
    if outline and not outline.get("skipped"):
        issues = outline.get("issues", [])
        if isinstance(issues, list) and issues:
            lines.append("")
            lines.append("## 章节结构（LLM 分治）")
            if outline.get("summary"):
                lines.append(f"- 总评：{_md_escape(str(outline.get('summary', '') or ''))}")
            for it in issues[:12]:
                if not isinstance(it, dict):
                    continue
                lines.append(f"- 问题：{_md_escape(str(it.get('problem', '') or ''))}")
                if it.get("detail"):
                    lines.append(f"  - 细节：{_md_escape(str(it.get('detail', '') or ''))}")
                if it.get("suggestion"):
                    lines.append(f"  - 建议：{_md_escape(str(it.get('suggestion', '') or ''))}")

    # Lexical stats
    if isinstance(lexical, dict):
        rare = lexical.get("rare_in_exemplars", None)
    else:
        rare = None

    lines.append("")
    lines.append("## 词语层面（统计）")
    if not isinstance(rare, dict):
        lib = _md_escape(str(meta.get("exemplar_library", "") or ""))
        hint = "（先构建范文库词频库）" if lib else "（先构建范文库词频库）"
        lines.append(f"- 未启用：当前没有可用的范文库词频统计{hint}。")
        if lib:
            lines.append(f"- 运行：`python scripts/build_vocab_library.py --library {lib}`")
    else:
        en = rare.get("en", [])
        zh = rare.get("zh", [])
        if (not (isinstance(en, list) and en)) and (not (isinstance(zh, list) and zh)):
            lines.append("- 未发现明显的“高频但范文罕见”词语（不代表没有问题，只是该项阈值未触发）。")
        else:
            lines.append("- 说明：这些词在你的稿子里出现较多，但在范文库中相对少见（不一定是错，可能是领域术语/变量名）。")
            if isinstance(en, list) and en:
                lines.append("- 英文（Top）：")
                for it in en[:15]:
                    if not isinstance(it, dict):
                        continue
                    lines.append(f"  - `{_md_escape(str(it.get('token', '') or ''))}` · 你的出现 {it.get('paper_count', 0)} · 范文 doc_freq {it.get('exemplar_doc_freq', 0)}")
            if isinstance(zh, list) and zh:
                lines.append("- 中文（Top）：")
                for it in zh[:15]:
                    if not isinstance(it, dict):
                        continue
                    lines.append(f"  - `{_md_escape(str(it.get('token', '') or ''))}` · 你的出现 {it.get('paper_count', 0)} · 范文 doc_freq {it.get('exemplar_doc_freq', 0)}")

    # Lexical LLM review
    lex_pack = llm_reviews.get("lexical", {}) if isinstance(llm_reviews.get("lexical", {}), dict) else {}
    lex_items = lex_pack.get("items", []) if isinstance(lex_pack.get("items", []), list) else []
    if lex_pack or lex_items:
        lines.append("")
        lines.append("## 词语层面（LLM 分治）")
        if lex_pack.get("skipped") and not lex_items:
            lines.append(f"- 已跳过：{_md_escape(str(lex_pack.get('reason', '') or 'unknown'))}")
        elif not lex_items:
            lines.append("- 暂无产出：可能被预算限制 / 检索未命中 / 或已在覆盖缓存中标记为已检查。")
        else:
            for it in lex_items[:25]:
                if not isinstance(it, dict):
                    continue
                tok = str(it.get("token", "") or "").strip()
                lang = str(it.get("language", "") or "").strip()
                page = _int(it.get("page", 0), 0)
                pc = _int(it.get("paper_count", 0), 0)
                df = _int(it.get("exemplar_doc_freq", 0), 0)
                if tok:
                    lines.append(f"- `{_md_escape(tok)}` · p{page} · 你的出现 {pc} · 范文 doc_freq {df} · {lang}")
                ctx = str(it.get("context_sentence", "") or "").strip()
                if ctx:
                    lines.append(f"  - 例句：{_md_escape(ctx)}")
                diags = it.get("diagnosis", [])
                if isinstance(diags, list):
                    for d in diags[:2]:
                        if not isinstance(d, dict):
                            continue
                        if d.get("problem"):
                            lines.append(f"  - 问题：{_md_escape(str(d.get('problem', '') or ''))}")
                        if d.get("suggestion"):
                            lines.append(f"  - 建议：{_md_escape(str(d.get('suggestion', '') or ''))}")
                        ev_md = _render_evidence(d.get("evidence", []))
                        if ev_md:
                            lines.append("  " + ev_md.replace("\n", "\n  "))
                temps = it.get("templates", [])
                if isinstance(temps, list) and temps:
                    lines.append("  - 可复用模板：")
                    for t in temps[:3]:
                        if not isinstance(t, dict):
                            continue
                        tx = str(t.get("text", "") or "").strip()
                        if tx:
                            lines.append(f"    - `{_md_escape(tx)}`")

    # Sentence-level (prefer LLM-reviewed items for actionable, white-box output)
    items = r.get("items", [])
    if isinstance(items, list) and items:
        sent_by_id: Dict[int, Dict[str, Any]] = {}
        for it in items:
            if isinstance(it, dict):
                sid = _int(it.get("id", -1), -1)
                if sid >= 0:
                    sent_by_id[sid] = it

        sent_pack = llm_reviews.get("sentence_alignment", {}) if isinstance(llm_reviews.get("sentence_alignment", {}), dict) else {}
        sent_reviews = sent_pack.get("items", []) if isinstance(sent_pack.get("items", []), list) else []

        lines.append("")
        if sent_reviews:
            lines.append("## 句子级：哪里不像范文（LLM 分治 + 证据）")
            shown = 0
            for rv in sent_reviews:
                if shown >= 60:
                    break
                if not isinstance(rv, dict):
                    continue
                sid = _int(rv.get("id", -1), -1)
                src = sent_by_id.get(sid, {})
                page = _int(src.get("page", 0), 0)
                text = str(src.get("text", "") or "").strip()
                align = src.get("alignment", {}) if isinstance(src.get("alignment", {}), dict) else {}
                pct = _int(align.get("pct", 0), 0)
                if not text:
                    continue
                lines.append(f"- p{page} · 对齐度 {pct}% · {_md_escape(text)}")

                diags = rv.get("diagnosis", [])
                if isinstance(diags, list) and diags:
                    for d in diags[:3]:
                        if not isinstance(d, dict):
                            continue
                        if d.get("problem"):
                            lines.append(f"  - 问题：{_md_escape(str(d.get('problem', '') or ''))}")
                        if d.get("suggestion"):
                            lines.append(f"  - 建议：{_md_escape(str(d.get('suggestion', '') or ''))}")
                        ev_md = _render_evidence(d.get("evidence", []))
                        if ev_md:
                            lines.append("  " + ev_md.replace("\n", "\n  "))
                temps = rv.get("templates", [])
                if isinstance(temps, list) and temps:
                    lines.append("  - 可复用模板：")
                    for t in temps[:4]:
                        if not isinstance(t, dict):
                            continue
                        tx = str(t.get("text", "") or "").strip()
                        if tx:
                            lines.append(f"    - `{_md_escape(tx)}`")
                shown += 1
        else:
            lines.append("## 句子级：哪里不像范文（对齐度筛选）")
            shown = 0
            for it in items:
                if shown >= 60:
                    break
                if not isinstance(it, dict):
                    continue
                issues = it.get("issues", []) or []
                has_low = any(isinstance(x, dict) and x.get("issue_type") == "low_alignment" for x in (issues if isinstance(issues, list) else []))
                if not has_low:
                    continue
                page = _int(it.get("page", 0), 0)
                text = str(it.get("text", "") or "").strip()
                align = it.get("alignment", {}) if isinstance(it.get("alignment", {}), dict) else {}
                pct = _int(align.get("pct", 0), 0)
                lines.append(f"- p{page} · 对齐度 {pct}% · {_md_escape(text)}")
                shown += 1

    # Paragraph-level
    para_pack = llm_reviews.get("paragraph_alignment", {}) if isinstance(llm_reviews.get("paragraph_alignment", {}), dict) else {}
    para_items = para_pack.get("items", []) if isinstance(para_pack.get("items", []), list) else []
    if para_items:
        lines.append("")
        lines.append("## 段落级：结构/语气问题（LLM 分治）")
        for it in para_items[:20]:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("paragraph_id", "") or "").strip()
            page = _int(it.get("page", 0), 0)
            if pid:
                lines.append(f"- {pid} · p{page}")
            diags = it.get("diagnosis", [])
            if isinstance(diags, list):
                for d in diags[:3]:
                    if not isinstance(d, dict):
                        continue
                    if d.get("problem"):
                        lines.append(f"  - 问题：{_md_escape(str(d.get('problem', '') or ''))}")
                    if d.get("suggestion"):
                        lines.append(f"  - 建议：{_md_escape(str(d.get('suggestion', '') or ''))}")
                    ev_md = _render_evidence(d.get("evidence", []))
                    if ev_md:
                        lines.append("  " + ev_md.replace("\n", "\n  "))
            temps = it.get("templates", [])
            if isinstance(temps, list) and temps:
                lines.append("  - 可复用模板：")
                for t in temps[:4]:
                    if not isinstance(t, dict):
                        continue
                    tx = str(t.get("text", "") or "").strip()
                    if tx:
                        lines.append(f"    - `{_md_escape(tx)}`")

    # Citation style
    cite_pack = llm_reviews.get("citation_style", {}) if isinstance(llm_reviews.get("citation_style", {}), dict) else {}
    cite_items = cite_pack.get("items", []) if isinstance(cite_pack.get("items", []), list) else []
    if cite_items:
        lines.append("")
        lines.append("## 引用句子写法（LLM 分治）")
        for it in cite_items[:20]:
            if not isinstance(it, dict):
                continue
            page = _int(it.get("page", 0), 0)
            sent = str(it.get("sentence", "") or "").strip()
            if sent:
                lines.append(f"- p{page} · {_md_escape(sent)}")
            diags = it.get("diagnosis", [])
            if isinstance(diags, list):
                for d in diags[:2]:
                    if not isinstance(d, dict):
                        continue
                    if d.get("problem"):
                        lines.append(f"  - 问题：{_md_escape(str(d.get('problem', '') or ''))}")
                    if d.get("suggestion"):
                        lines.append(f"  - 建议：{_md_escape(str(d.get('suggestion', '') or ''))}")
                    ev_md = _render_evidence(d.get("evidence", []))
                    if ev_md:
                        lines.append("  " + ev_md.replace("\n", "\n  "))
            temps = it.get("templates", [])
            if isinstance(temps, list) and temps:
                lines.append("  - 模板：")
                for t in temps[:3]:
                    if not isinstance(t, dict):
                        continue
                    tx = str(t.get("text", "") or "").strip()
                    if tx:
                        lines.append(f"    - `{_md_escape(tx)}`")

    # CiteCheck
    citecheck = r.get("citecheck", {}) if isinstance(r.get("citecheck", {}), dict) else {}
    if citecheck:
        meta2 = citecheck.get("meta", {}) if isinstance(citecheck.get("meta", {}), dict) else {}
        counts = citecheck.get("counts", {}) if isinstance(citecheck.get("counts", {}), dict) else {}
        citems = citecheck.get("items", []) if isinstance(citecheck.get("items", []), list) else []
        if meta2.get("skipped"):
            lines.append("")
            lines.append("## 引用准确性（CiteCheck）")
            lines.append(f"- 已跳过：{_md_escape(str(meta2.get('reason', '') or ''))}")
        elif citems:
            lines.append("")
            lines.append("## 引用准确性（CiteCheck）")
            lines.append(f"- 引用对数量：{_int(len(citems), 0)}")
            if counts:
                parts = []
                for k in sorted(counts.keys()):
                    parts.append(f"{k}={_int(counts.get(k, 0), 0)}")
                if parts:
                    lines.append("- Verdict 统计：" + ", ".join(parts))
            shown = 0
            for it in citems:
                if shown >= 40:
                    break
                if not isinstance(it, dict):
                    continue
                verdict = str(it.get("verdict", "") or "").strip()
                if not verdict:
                    continue
                if verdict.upper() == "ACCURATE":
                    continue
                page = _int(it.get("page_in_main", 0), 0)
                a = str(it.get("cited_author", "") or "").strip()
                y = str(it.get("cited_year", "") or "").strip()
                head = f"{a} ({y})".strip() if (a or y) else "(unknown)"
                lines.append(f"- p{page} · {verdict} · {_md_escape(head)}")
                if it.get("reason"):
                    lines.append(f"  - 原因：{_md_escape(str(it.get('reason', '') or ''))}")
                if it.get("suggested_fix"):
                    lines.append(f"  - 低风险改写：{_md_escape(str(it.get('suggested_fix', '') or ''))}")
                shown += 1

    return "\n".join(lines).strip() + "\n"
