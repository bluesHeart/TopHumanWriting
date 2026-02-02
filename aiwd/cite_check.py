# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None

from aiwd.citeextract.pipeline import iter_citation_sentences_from_pages, load_pdf_pages
from aiwd.citeextract.references import ReferenceEntry, iter_reference_entries_from_pages
from aiwd.llm_budget import LLMBudget, approx_tokens
from aiwd.openai_compat import OpenAICompatClient, extract_first_content, extract_usage
from aiwd.review_coverage import ReviewCoverageStore, stable_text_key


class CiteCheckError(RuntimeError):
    pass


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _file_sig(path: str) -> dict:
    try:
        st = os.stat(path)
        return {"size": int(st.st_size), "mtime": int(st.st_mtime)}
    except Exception:
        return {"size": 0, "mtime": 0}


def _hash_key(s: str) -> str:
    return hashlib.md5((s or "").encode("utf-8", errors="ignore")).hexdigest()


_REF_TITLE_QUOTED_RE = re.compile(r'"([^"]{20,})"')


def extract_reference_title(reference_text: str) -> str:
    """Best-effort title extraction from a reference entry (no LLM)."""
    ref_text = _normalize_ws(reference_text)
    if not ref_text:
        return ""

    # Strategy 1: the first long quoted span.
    m = _REF_TITLE_QUOTED_RE.search(ref_text)
    if m:
        return (m.group(1) or "").strip()[:200]

    # Strategy 2: text after the year marker (common APA-ish patterns).
    ym = re.search(r"\(?\d{4}\)?[.,]?\s*", ref_text)
    if ym:
        after = ref_text[ym.end() :].strip()
        m2 = re.match(r"([^.]+)", after)
        if m2:
            title = (m2.group(1) or "").strip().strip("\"'")
            if len(title) >= 12:
                return title[:200]
        return after[:150]

    return ref_text[:120]


def _surname_tokens(authors: str) -> List[str]:
    authors = (authors or "").strip()
    if not authors:
        return []
    surnames: List[str] = []
    for part in re.split(r"\s+and\s+|\s*&\s*", authors):
        part = (part or "").strip()
        if not part:
            continue
        part = re.sub(r"\s+(?:et\s+al\.?|等人?|等)\s*$", "", part, flags=re.IGNORECASE).strip()
        if "," in part:
            surn = (part.split(",")[0] or "").strip()
            if surn:
                surnames.append(surn.lower())
            continue
        words = [w.strip(",") for w in part.split() if w and w[0].isalpha()]
        if not words:
            continue
        w_last = (words[-1] or "").strip()
        if w_last.lower() in ("al", "al.") and len(words) >= 2:
            # "Smith et al." -> Smith
            if len(words) >= 3 and words[-2].lower() == "et":
                surnames.append(words[-3].lower())
            else:
                surnames.append(words[-2].lower())
        else:
            surnames.append(w_last.lower())
    seen = set()
    out: List[str] = []
    for s in surnames:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def match_reference_entry(
    *,
    cited_author: str,
    cited_year: str,
    references: Sequence[ReferenceEntry],
) -> Optional[ReferenceEntry]:
    """Map a (author, year) citation to a ReferenceEntry using simple heuristics."""
    year = (cited_year or "").strip()
    want = _surname_tokens(cited_author)
    if not want or not year:
        return None

    def _has_cjk(s: str) -> bool:
        for ch in (s or ""):
            if "\u4e00" <= ch <= "\u9fff":
                return True
        return False

    best: Tuple[float, Optional[ReferenceEntry]] = (0.0, None)
    for ref in references:
        try:
            ref_year = (ref.year or "").strip()
            if ref_year != year:
                continue
            ref_auth = (ref.authors or "").lower()
            hit = 0
            for s in want:
                s = (s or "").strip().lower()
                if not s:
                    continue
                if _has_cjk(s):
                    if s in ref_auth:
                        hit += 1
                    continue
                if len(s) >= 3:
                    if s in ref_auth:
                        hit += 1
                    continue
                # Short latin surnames (e.g. "Li") need word-boundary match.
                if len(s) >= 2 and re.search(rf"\b{re.escape(s)}\b", ref_auth):
                    hit += 1
            score = hit / max(1, len(want))
            if score <= 0:
                continue
            if score > best[0]:
                best = (score, ref)
        except Exception:
            continue
    return best[1]


@dataclass(frozen=True)
class EvidenceParagraph:
    page: int
    score: float
    text: str


@dataclass(frozen=True)
class CiteCheckItem:
    page_in_main: int
    original_sentence: str
    cited_author: str
    cited_year: str
    ref_title: str
    reference_entry: str
    ref_missing: bool
    matched_pdf: str
    matched_pdf_rel: str
    verdict: str
    confidence: float
    claim: str
    reason: str
    suggested_fix: str
    evidence: List[EvidenceParagraph]

    def to_dict(self) -> dict:
        return {
            "page_in_main": int(self.page_in_main or 0),
            "original_sentence": self.original_sentence,
            "cited_author": self.cited_author,
            "cited_year": self.cited_year,
            "ref_title": self.ref_title,
            "reference_entry": self.reference_entry,
            "ref_missing": bool(self.ref_missing),
            "matched_pdf": self.matched_pdf,
            "matched_pdf_rel": self.matched_pdf_rel,
            "verdict": self.verdict,
            "confidence": float(self.confidence or 0.0),
            "claim": self.claim,
            "reason": self.reason,
            "suggested_fix": self.suggested_fix,
            "evidence": [asdict(e) for e in (self.evidence or [])],
        }


@dataclass(frozen=True)
class CiteCheckConfig:
    title_match_threshold: float = 0.55
    paragraph_top_k: int = 5
    max_pairs: int = 80
    use_llm: bool = True
    llm_timeout_s: float = 90.0


class PapersTitleIndex:
    def __init__(
        self,
        *,
        cache_dir: str,
        papers_root: str,
        embed_texts: Callable[[List[str]], "np.ndarray"],
        model_fingerprint: dict,
    ):
        if np is None:
            raise CiteCheckError("numpy is required")
        self.cache_dir = cache_dir
        self.papers_root = os.path.abspath(papers_root)
        self.embed_texts = embed_texts
        self.model_fingerprint = model_fingerprint or {}

        self.entries: List[dict] = []
        self.vecs = None

    @staticmethod
    def _iter_pdfs(root: str) -> List[str]:
        p = Path(root)
        return [str(x) for x in p.rglob("*.pdf")]

    def _cache_paths(self) -> Tuple[str, str, str]:
        root_key = _hash_key(self.papers_root)
        base = os.path.join(self.cache_dir, "papers", root_key)
        meta = os.path.join(base, "manifest.json")
        entries = os.path.join(base, "entries.jsonl")
        vecs = os.path.join(base, "title_embeddings.npy")
        return meta, entries, vecs

    def _load_cache(self) -> bool:
        meta_path, entries_path, vecs_path = self._cache_paths()
        try:
            if not (os.path.exists(meta_path) and os.path.exists(entries_path) and os.path.exists(vecs_path)):
                return False
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                return False
            if os.path.abspath(str(meta.get("papers_root", "") or "")) != self.papers_root:
                return False
            if meta.get("model_fingerprint", {}) != (self.model_fingerprint or {}):
                return False

            files = meta.get("files", {})
            if not isinstance(files, dict):
                return False
            for rel, sig in files.items():
                full = os.path.join(self.papers_root, str(rel).replace("\\", "/"))
                if not os.path.exists(full):
                    return False
                if _file_sig(full) != (sig or {}):
                    return False

            entries: List[dict] = []
            with open(entries_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        entries.append(obj)
            if not entries:
                return False
            vecs = np.load(vecs_path, allow_pickle=False)
            if int(getattr(vecs, "shape", [0])[0] or 0) != len(entries):
                return False

            self.entries = entries
            self.vecs = vecs
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        meta_path, entries_path, vecs_path = self._cache_paths()
        base = os.path.dirname(meta_path)
        os.makedirs(base, exist_ok=True)

        entries_tmp = entries_path + ".tmp"
        with open(entries_tmp, "w", encoding="utf-8") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(entries_tmp, entries_path)

        vecs_tmp = vecs_path + ".tmp"
        np.save(vecs_tmp, self.vecs)
        vecs_tmp2 = vecs_tmp if vecs_tmp.endswith(".npy") else vecs_tmp + ".npy"
        os.replace(vecs_tmp2, vecs_path)

        files = {}
        for e in self.entries:
            rel = str(e.get("rel", "") or "")
            if not rel:
                continue
            files[rel] = _file_sig(os.path.join(self.papers_root, rel))
        meta = {
            "papers_root": self.papers_root,
            "model_fingerprint": self.model_fingerprint or {},
            "updated_at": int(time.time()),
            "files": files,
        }
        meta_tmp = meta_path + ".tmp"
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(meta_tmp, meta_path)

    def build(
        self,
        *,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> None:
        if self._load_cache():
            return

        pdfs = self._iter_pdfs(self.papers_root)
        entries: List[dict] = []
        texts: List[str] = []
        total = int(len(pdfs))
        for i, p in enumerate(pdfs, start=1):
            try:
                if cancel_cb and cancel_cb():
                    raise CiteCheckError("canceled")
            except CiteCheckError:
                raise
            except Exception:
                pass

            if callable(progress_cb):
                try:
                    progress_cb(int(i), total, os.path.basename(p))
                except Exception:
                    pass
            try:
                rel = os.path.relpath(p, self.papers_root).replace("\\", "/")
            except Exception:
                rel = os.path.basename(p).replace("\\", "/")
            pages = load_pdf_pages(Path(p), max_pages=2)
            head = "\n".join((pages or [])[:2])
            title_area = (head or "")[:500]
            match_text = f"{os.path.basename(p)} {title_area}"
            entries.append({"rel": rel, "filename": os.path.basename(p), "title_area": title_area})
            texts.append(match_text[:700])

        self.entries = entries
        if entries:
            self.vecs = self.embed_texts(texts)
        else:
            self.vecs = np.zeros((0, 1), dtype=np.float32)
        self._save_cache()

    def find_by_author_year(self, author: str, year: str) -> Optional[dict]:
        author = (author or "").strip()
        year = (year or "").strip()
        if not author or not year:
            return None
        surnames = _surname_tokens(author)
        if not surnames:
            return None

        best = None
        best_score = 0.0
        for e in self.entries:
            name = str(e.get("filename", "") or "").lower()
            if year not in name:
                continue
            hit = 0
            for s in surnames:
                if s and s in name:
                    hit += 1
            if hit <= 0:
                continue
            score = hit / max(1, len(surnames))
            if score > best_score:
                best_score = score
                best = e
        return best

    def find_by_title(self, title: str, authors: str = "", *, threshold: Optional[float] = None) -> Optional[dict]:
        if np is None:
            return None
        title = _normalize_ws(title)
        if not title:
            return None
        if not self.entries or self.vecs is None:
            return None
        q = _normalize_ws(f"{title} {authors}")
        qv = self.embed_texts([q])
        try:
            qv1 = np.asarray(qv[0], dtype=np.float32)
        except Exception:
            return None
        sims = np.dot(self.vecs, qv1)
        best_idx = int(np.argmax(sims))
        best_score = float(sims[best_idx])
        thr = float(threshold) if threshold is not None else 0.55
        if best_score < thr:
            return None
        try:
            e = self.entries[best_idx]
            return {**e, "score": best_score}
        except Exception:
            return None

    def search(self, query: str, *, top_k: int = 5) -> List[dict]:
        """Embedding search over (filename + first-page title area) to suggest candidate PDFs."""
        if np is None:
            return []
        q = _normalize_ws(query)
        if not q:
            return []
        if not self.entries or self.vecs is None:
            return []
        try:
            qv = self.embed_texts([q])
            qv1 = np.asarray(qv[0], dtype=np.float32)
        except Exception:
            return []
        sims = np.dot(self.vecs, qv1)
        k = max(1, min(int(top_k or 0), 12))
        idxs = np.argsort(sims)[-k:][::-1]
        out: List[dict] = []
        for i in idxs:
            ii = int(i)
            if ii < 0 or ii >= len(self.entries):
                continue
            try:
                e = dict(self.entries[ii])
            except Exception:
                continue
            e["score"] = float(sims[ii])
            out.append(e)
        return out


class ParagraphIndex:
    def __init__(
        self,
        *,
        cache_dir: str,
        pdf_path: str,
        embed_texts: Callable[[List[str]], "np.ndarray"],
        model_fingerprint: dict,
    ):
        if np is None:
            raise CiteCheckError("numpy is required")
        self.cache_dir = cache_dir
        self.pdf_path = os.path.abspath(pdf_path)
        self.embed_texts = embed_texts
        self.model_fingerprint = model_fingerprint or {}
        self.paragraphs: List[dict] = []
        self.vecs = None

    def _cache_paths(self) -> Tuple[str, str, str]:
        key = _hash_key(self.pdf_path)
        base = os.path.join(self.cache_dir, "para", key)
        meta = os.path.join(base, "meta.json")
        paras = os.path.join(base, "paragraphs.jsonl")
        vecs = os.path.join(base, "embeddings.npy")
        return meta, paras, vecs

    def _load_cache(self) -> bool:
        meta_path, paras_path, vecs_path = self._cache_paths()
        try:
            if not (os.path.exists(meta_path) and os.path.exists(paras_path) and os.path.exists(vecs_path)):
                return False
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            if not isinstance(meta, dict):
                return False
            if os.path.abspath(str(meta.get("pdf_path", "") or "")) != self.pdf_path:
                return False
            if meta.get("model_fingerprint", {}) != (self.model_fingerprint or {}):
                return False
            if meta.get("file_sig", {}) != _file_sig(self.pdf_path):
                return False

            paras: List[dict] = []
            with open(paras_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        paras.append(obj)
            if not paras:
                return False
            vecs = np.load(vecs_path, allow_pickle=False)
            if int(getattr(vecs, "shape", [0])[0] or 0) != len(paras):
                return False

            self.paragraphs = paras
            self.vecs = vecs
            return True
        except Exception:
            return False

    def _save_cache(self) -> None:
        meta_path, paras_path, vecs_path = self._cache_paths()
        base = os.path.dirname(meta_path)
        os.makedirs(base, exist_ok=True)

        paras_tmp = paras_path + ".tmp"
        with open(paras_tmp, "w", encoding="utf-8") as f:
            for p in self.paragraphs:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        os.replace(paras_tmp, paras_path)

        vecs_tmp = vecs_path + ".tmp"
        np.save(vecs_tmp, self.vecs)
        vecs_tmp2 = vecs_tmp if vecs_tmp.endswith(".npy") else vecs_tmp + ".npy"
        os.replace(vecs_tmp2, vecs_path)

        meta = {
            "pdf_path": self.pdf_path,
            "file_sig": _file_sig(self.pdf_path),
            "model_fingerprint": self.model_fingerprint or {},
            "updated_at": int(time.time()),
        }
        meta_tmp = meta_path + ".tmp"
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(meta_tmp, meta_path)

    @staticmethod
    def _split_paragraphs(page_text: str) -> List[str]:
        raw = (page_text or "").replace("\r\n", "\n").replace("\r", "\n")
        parts = re.split(r"\n\s*\n+", raw)
        out: List[str] = []
        for p in parts:
            p = _normalize_ws(p)
            if not p:
                continue
            out.append(p)
        return out

    def build(
        self,
        *,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> None:
        if self._load_cache():
            return
        pages = load_pdf_pages(Path(self.pdf_path))
        paras: List[dict] = []
        texts: List[str] = []

        total_pages = int(len(pages or []))
        stop = False
        for page_num, page_text in enumerate(pages or [], start=1):
            if stop:
                break
            try:
                if cancel_cb and cancel_cb():
                    raise CiteCheckError("canceled")
            except CiteCheckError:
                raise
            except Exception:
                pass

            if callable(progress_cb):
                try:
                    progress_cb(int(page_num), total_pages, os.path.basename(self.pdf_path))
                except Exception:
                    pass
            m = re.search(r"(?:^|\n)\s*(references|bibliography|参考文献|引用文献)\s*(?:\n|$)", page_text or "", flags=re.IGNORECASE)
            if m:
                head = (page_text or "")[: m.start()]
                if _normalize_ws(head):
                    page_text = head
                stop = True

            for chunk in self._split_paragraphs(page_text or ""):
                if len(chunk) < 50:
                    continue
                if "." not in chunk and "。" not in chunk:
                    continue
                paras.append({"page": int(page_num), "text": chunk})
                texts.append(chunk)

        self.paragraphs = paras
        if texts:
            self.vecs = self.embed_texts(texts)
        else:
            self.vecs = np.zeros((0, 1), dtype=np.float32)
        self._save_cache()

    def search(self, query: str, *, top_k: int = 5) -> List[EvidenceParagraph]:
        if np is None:
            return []
        query = _normalize_ws(query)
        if not query:
            return []
        if not self.paragraphs or self.vecs is None:
            return []
        qv = self.embed_texts([query])
        try:
            qv1 = np.asarray(qv[0], dtype=np.float32)
        except Exception:
            return []
        sims = np.dot(self.vecs, qv1)
        k = max(1, min(int(top_k or 0), 12))
        idxs = np.argsort(sims)[-k:][::-1]
        out: List[EvidenceParagraph] = []
        for i in idxs:
            ii = int(i)
            if ii < 0 or ii >= len(self.paragraphs):
                continue
            p = self.paragraphs[ii]
            out.append(EvidenceParagraph(page=int(p.get("page", 0) or 0), score=float(sims[ii]), text=str(p.get("text", "") or "")))
        return out


def verify_citation_with_llm(
    *,
    llm: OpenAICompatClient,
    citation_sentence: str,
    cited_author: str,
    cited_year: str,
    ref_title: str,
    paper_summary: str,
    evidence: Sequence[EvidenceParagraph],
    timeout_s: float = 90.0,
    budget: Optional[LLMBudget] = None,
) -> dict:
    if not evidence:
        return {"verdict": "NOT_FOUND", "confidence": 0.0, "claim": "", "reason": "未找到相关段落", "suggested_fix": ""}

    # If we've already detected a non-recoverable auth/validation issue, avoid
    # repeatedly calling the API for every citation pair.
    try:
        if budget is not None and any(str(w or "").startswith("llm_blocked:") for w in (budget.warnings or [])):
            return {"verdict": "EVIDENCE_ONLY", "confidence": 0.0, "claim": "", "reason": "LLM 不可用（账号验证/权限问题），跳过判定。", "suggested_fix": ""}
    except Exception:
        pass

    def _extract_json_obj(text: str) -> Optional[dict]:
        s = (text or "").strip()
        if not s:
            return None
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s)
            s = re.sub(r"\s*```$", "", s).strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except Exception:
            pass
        try:
            start = s.find("{")
            end = s.rfind("}")
            if start >= 0 and end > start:
                obj = json.loads(s[start : end + 1])
                return obj if isinstance(obj, dict) else None
        except Exception:
            return None
        return None

    ev_text = "\n\n".join([f"[P{i+1}, Page {p.page}] {p.text[:400]}" for i, p in enumerate(list(evidence)[:3])])
    prompt = f"""验证学术引用准确性（白箱）。请只依据【相关段落】判断，不要编造。

【引用句】\"{(citation_sentence or '')[:320]}\"
【被引文献】{cited_author} ({cited_year}), \"{(ref_title or '')[:120]}\"

【论文摘要区】{(paper_summary or '')[:900]}

【相关段落】
{ev_text}

【任务】
1) 用一句话提炼【引用句】里的“被引用论点”（claim）。
2) 判断：该论点是否真能从【相关段落】推出？
3) 如不准确，给出一条“低风险改写”（suggested_fix），尽量保留原句语气/结构，避免新增事实；不确定时用更弱的表述。

【输出 JSON】必须只输出一个 JSON 对象，字段如下：
{{\"verdict\":\"X\",\"confidence\":0.9,\"claim\":\"...\",\"reason\":\"...\",\"suggested_fix\":\"...\"}}

verdict 取值:
- ACCURATE: 论文确实表达了引用所述观点
- INACCURATE: 论点有偏差或曲解
- MISATTRIBUTED: 观点存在但不是该作者的贡献
- NOT_FOUND: 找不到支持依据

输出要求:
- 只输出 JSON（不要 Markdown / 代码块 / 多余文字）
- claim ≤ 200 字符；suggested_fix ≤ 280 字符（ACCURATE 时可为空字符串）
"""

    last_err = ""
    last_cleaned = ""
    # Some providers (e.g. Gemini-style gateways) count internal reasoning tokens into
    # `max_tokens`, which can truncate a small JSON output if the budget is too low.
    # Use a larger budget by default and retry once with a bigger cap.
    base = 4096
    token_budget = [base, max(8192, base)]
    for attempt in range(2):
        prompt2 = prompt
        if attempt >= 1:
            prompt2 = (
                prompt
                + "\n\n你上一次没有输出有效 JSON。请严格只输出一个 JSON 对象，必须包含 verdict/confidence/claim/reason/suggested_fix 五个字段，不要 Markdown/列表/多余文字。"
            )

        max_tok = int(token_budget[min(attempt, len(token_budget) - 1)])
        if budget is not None:
            pt = approx_tokens("Return STRICT JSON only.") + approx_tokens(prompt2)
            if budget.would_exceed_budget(approx_prompt_tokens=pt, max_completion_tokens=max_tok):
                budget.warnings.append("budget_exceeded: citecheck llm skipped")
                return {"verdict": "EVIDENCE_ONLY", "confidence": 0.0, "claim": "", "reason": "LLM 预算不足，跳过判定。", "suggested_fix": ""}

        status, resp = llm.chat(
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": prompt2},
            ],
            temperature=0.0,
            max_tokens=max_tok,
            response_format={"type": "json_object"},
            timeout_s=float(timeout_s or 90.0),
        )

        content = extract_first_content(resp)
        if budget is not None:
            usage = extract_usage(resp)
            if usage.get("total_tokens", 0) > 0:
                budget.add_usage(usage)
            else:
                budget.add_approx(prompt2, content)

        if int(status or 0) != 200:
            raw = ""
            try:
                if isinstance(resp, dict):
                    if isinstance(resp.get("error", None), dict):
                        raw = (resp.get("error", {}) or {}).get("message", "") or ""
                    if not raw:
                        raw = str(resp.get("_raw", "") or resp.get("_error", "") or "").strip()
            except Exception:
                raw = ""

            # Surface important API failures to the user via budget warnings.
            try:
                if budget is not None:
                    low = (raw or "").lower()
                    if int(status or 0) == 403 and ("verify your account" in low or "validation_required" in low):
                        n = 0
                        try:
                            n = int(budget.inc_error("403_validation_required") or 0)
                        except Exception:
                            n = 0
                        if "llm_error:403_validation_required" not in (budget.warnings or []):
                            budget.warnings.append("llm_error:403_validation_required")
                        if n >= 3 and "llm_blocked:403_validation_required" not in (budget.warnings or []):
                            budget.warnings.append("llm_blocked:403_validation_required")
                    else:
                        w = f"llm_error:http_{int(status or 0)}"
                        if w not in (budget.warnings or []):
                            budget.warnings.append(w)
            except Exception:
                pass

            last_err = raw or f"api request failed (HTTP {status})"
            continue

        # If the provider explicitly reports truncation, retry with a larger budget.
        try:
            fr = ""
            if isinstance(resp, dict):
                choices = resp.get("choices", [])
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    fr = str(choices[0].get("finish_reason", "") or "").strip().lower()
            if fr == "length":
                last_err = "truncated json"
                continue
        except Exception:
            pass

        cleaned = (content or "").strip()
        last_cleaned = cleaned
        data = _extract_json_obj(cleaned)

        if isinstance(data, dict):
            verdict = str(data.get("verdict", "") or "").strip().upper()
            if verdict.startswith("ACC"):
                verdict = "ACCURATE"
            elif verdict.startswith("INACC"):
                verdict = "INACCURATE"
            elif verdict.startswith("MIS"):
                verdict = "MISATTRIBUTED"
            elif verdict.startswith("NOT"):
                verdict = "NOT_FOUND"
            if verdict in ("ACCURATE", "INACCURATE", "MISATTRIBUTED", "NOT_FOUND"):
                try:
                    conf = float(data.get("confidence", 0.5))
                except Exception:
                    conf = 0.5
                claim = str(data.get("claim", "") or "").strip()
                reason = str(data.get("reason", "") or "").strip()
                suggested_fix = str(data.get("suggested_fix", "") or "").strip()
                if len(claim) > 220:
                    claim = claim[:220] + "…"
                if len(reason) > 800:
                    reason = reason[:800] + "…"
                if len(suggested_fix) > 320:
                    suggested_fix = suggested_fix[:320] + "…"
                return {"verdict": verdict, "confidence": conf, "claim": claim, "reason": reason, "suggested_fix": suggested_fix}

        # If the model started outputting JSON but it's clearly truncated, retry instead of
        # returning a half-parsed verdict/reason.
        if cleaned.lstrip().startswith("{") and not cleaned.rstrip().endswith("}"):
            last_err = "truncated json"
            continue

        verdict_m = re.search(r'"verdict"\s*:\s*"([A-Z_]+)', cleaned, re.IGNORECASE)
        conf_m = re.search(r'"confidence"\s*:\s*([\d.]+)', cleaned)
        claim_m = re.search(r'"claim"\s*:\s*"([^"]*)"?', cleaned)
        reason_m = re.search(r'"reason"\s*:\s*"([^"]*)"?', cleaned)
        fix_m = re.search(r'"suggested_fix"\s*:\s*"([^"]*)"?', cleaned)
        verdict = verdict_m.group(1).upper() if verdict_m else "PARSE_ERROR"
        if verdict.startswith("ACC"):
            verdict = "ACCURATE"
        elif verdict.startswith("INACC"):
            verdict = "INACCURATE"
        elif verdict.startswith("MIS"):
            verdict = "MISATTRIBUTED"
        elif verdict.startswith("NOT"):
            verdict = "NOT_FOUND"
        try:
            conf = float(conf_m.group(1)) if conf_m else 0.5
        except Exception:
            conf = 0.5
        claim = claim_m.group(1) if claim_m else ""
        reason = reason_m.group(1) if reason_m else (cleaned[:200] if cleaned else "")
        suggested_fix = fix_m.group(1) if fix_m else ""

        if verdict != "PARSE_ERROR":
            return {"verdict": verdict, "confidence": conf, "claim": claim, "reason": reason, "suggested_fix": suggested_fix}

    if last_err:
        msg = last_err.strip()
        if msg.lower().startswith("truncated json"):
            return {"verdict": "PARSE_ERROR", "confidence": 0.0, "claim": "", "reason": "LLM returned truncated JSON output", "suggested_fix": ""}
        return {"verdict": "ERROR", "confidence": 0.0, "claim": "", "reason": msg[:500], "suggested_fix": ""}
    return {"verdict": "PARSE_ERROR", "confidence": 0.5, "claim": "", "reason": (last_cleaned[:200] if last_cleaned else ""), "suggested_fix": ""}


class CiteCheckRunner:
    def __init__(self, *, data_dir: str, embed_texts: Callable[[List[str]], "np.ndarray"], model_fingerprint: dict):
        if np is None:
            raise CiteCheckError("numpy is required")
        self.data_dir = data_dir
        self.embed_texts = embed_texts
        self.model_fingerprint = model_fingerprint or {}
        self.cache_dir = os.path.join(data_dir, "citecheck", "cache")

    def find_missing_papers(
        self,
        *,
        main_pdf_path: str,
        papers_root: str,
        cfg: CiteCheckConfig,
        only_cited: bool = True,
        max_items: int = 200,
        cancel_cb: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> dict:
        """
        Find missing reference PDFs for a draft paper.

        This is an offline helper to reduce PDF_NOT_FOUND in the CiteCheck run.
        """
        if np is None:
            raise CiteCheckError("numpy is required")
        t0 = time.time()
        main_pdf_path = os.path.abspath(main_pdf_path)
        papers_root = os.path.abspath(papers_root)

        def report(stage: str, done: int, total: int, detail: str = ""):
            if callable(progress_cb):
                try:
                    progress_cb(stage, int(done or 0), int(total or 0), str(detail or ""))
                except Exception:
                    pass

        def canceled() -> bool:
            try:
                return bool(cancel_cb and cancel_cb())
            except Exception:
                return False

        pages = load_pdf_pages(Path(main_pdf_path))
        citations = list(iter_citation_sentences_from_pages(pages, pdf_label=os.path.basename(main_pdf_path), stop_at_references=True))
        references = list(iter_reference_entries_from_pages(pages, pdf_label=os.path.basename(main_pdf_path)))

        cited_ref_keys: set[tuple[str, str]] = set()
        # Map in-text citations to References entries (surname-only citations are common).
        for rec in citations:
            for c in rec.citations:
                a = (c.get("authors", "") or "").strip()
                y = (c.get("year", "") or "").strip()
                if not a or not y:
                    continue
                ref = match_reference_entry(cited_author=a, cited_year=y, references=references)
                if ref is not None:
                    key = ((ref.authors or "").strip(), (ref.year or "").strip())
                    if key[0] and key[1]:
                        cited_ref_keys.add(key)

        targets: List[tuple[str, str]] = []
        if only_cited:
            targets = sorted(cited_ref_keys)
        else:
            for r in references:
                a = (r.authors or "").strip()
                y = (r.year or "").strip()
                if a and y:
                    targets.append((a, y))
            targets = sorted(set(targets))

        if max_items and len(targets) > int(max_items):
            targets = targets[: int(max_items)]

        report("papers_index", 0, 1, "索引参考文献原文 PDF…")
        title_index = PapersTitleIndex(
            cache_dir=self.cache_dir,
            papers_root=papers_root,
            embed_texts=self.embed_texts,
            model_fingerprint=self.model_fingerprint,
        )
        title_index.build(progress_cb=lambda d, t, detail: report("papers_index", d, t, f"读取标题 · {detail}"), cancel_cb=canceled)
        report("papers_index", int(len(title_index.entries)), int(len(title_index.entries)), f"已索引 {len(title_index.entries)} 篇 PDF")

        missing: List[dict] = []
        report("missing_scan", 0, len(targets), "扫描缺失原文…")

        for idx, (author, year) in enumerate(targets, start=1):
            if canceled():
                break

            ref = match_reference_entry(cited_author=author, cited_year=year, references=references)
            ref_missing = ref is None
            ref_title = extract_reference_title(ref.reference) if ref is not None else ""
            entry = str(ref.reference or "") if ref is not None else ""

            picked = title_index.find_by_author_year(author, year)
            if not picked and ref_title and ref is not None:
                picked = title_index.find_by_title(ref_title, ref.authors or "", threshold=cfg.title_match_threshold)

            if picked:
                report("missing_scan", idx, len(targets), f"已匹配 · {author} ({year})")
                continue

            query = ref_title or f"{author} {year}"
            candidates0 = title_index.search(query, top_k=3)
            candidates = []
            for c in candidates0:
                try:
                    candidates.append({"rel": str(c.get("rel", "") or ""), "filename": str(c.get("filename", "") or ""), "score": float(c.get("score", 0.0) or 0.0)})
                except Exception:
                    continue

            if ref_missing:
                reason = "References 中未找到对应条目（可能缺失/格式不标准），且库中也未找到明显匹配的原文 PDF"
            else:
                reason = "未在当前库中找到匹配的原文 PDF（可先补齐参考文献原文 PDF）"

            missing.append(
                {
                    "cited_author": author,
                    "cited_year": year,
                    "ref_missing": bool(ref_missing),
                    "ref_title": ref_title,
                    "reference_entry": entry[:1200],
                    "reason": reason,
                    "candidates": candidates,
                }
            )
            report("missing_scan", idx, len(targets), f"缺失 · {author} ({year})")

        return {
            "meta": {
                "main_pdf": os.path.basename(main_pdf_path),
                "papers_root": papers_root,
                "created_at": int(time.time()),
                "seconds": float(time.time() - t0),
                "only_cited": bool(only_cited),
                "target_count": int(len(targets)),
            },
            "missing": missing,
            "missing_count": int(len(missing)),
            "citation_sentence_count": int(len(citations)),
            "reference_count": int(len(references)),
        }

    def run(
        self,
        *,
        main_pdf_path: str,
        papers_root: str,
        library_pdf_root: str,
        cfg: CiteCheckConfig,
        llm: Optional[OpenAICompatClient] = None,
        budget: Optional[LLMBudget] = None,
        coverage: Optional[ReviewCoverageStore] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
    ) -> dict:
        if np is None:
            raise CiteCheckError("numpy is required")
        t0 = time.time()
        main_pdf_path = os.path.abspath(main_pdf_path)
        papers_root = os.path.abspath(papers_root)
        library_pdf_root = os.path.abspath(library_pdf_root)

        def report(stage: str, done: int, total: int, detail: str = ""):
            if callable(progress_cb):
                try:
                    progress_cb(stage, int(done or 0), int(total or 0), str(detail or ""))
                except Exception:
                    pass

        def canceled() -> bool:
            try:
                return bool(cancel_cb and cancel_cb())
            except Exception:
                return False

        pages = load_pdf_pages(Path(main_pdf_path))
        citations = list(iter_citation_sentences_from_pages(pages, pdf_label=os.path.basename(main_pdf_path), stop_at_references=True))
        references = list(iter_reference_entries_from_pages(pages, pdf_label=os.path.basename(main_pdf_path)))

        cited_ref_keys: set[tuple[str, str]] = set()
        # Map in-text citations to References entries (surname-only citations are common).
        for rec in citations:
            for c in rec.citations:
                a = (c.get("authors", "") or "").strip()
                y = (c.get("year", "") or "").strip()
                if not a or not y:
                    continue
                ref = match_reference_entry(cited_author=a, cited_year=y, references=references)
                if ref is not None:
                    key = ((ref.authors or "").strip(), (ref.year or "").strip())
                    if key[0] and key[1]:
                        cited_ref_keys.add(key)

        uncited_refs: List[dict] = []
        for r in references:
            key = ((r.authors or "").strip(), (r.year or "").strip())
            if not key[0] or not key[1]:
                continue
            if key not in cited_ref_keys:
                uncited_refs.append(
                    {
                        "page": int(r.page or 0),
                        "index": int(r.index or 0),
                        "authors": r.authors,
                        "year": r.year,
                        "title": extract_reference_title(r.reference),
                        "reference": r.reference,
                    }
                )

        report("papers_index", 0, 1, "索引参考文献 PDF（标题匹配）…")
        title_index = PapersTitleIndex(
            cache_dir=self.cache_dir,
            papers_root=papers_root,
            embed_texts=self.embed_texts,
            model_fingerprint=self.model_fingerprint,
        )
        title_index.build(
            progress_cb=lambda d, t, detail: report("papers_index", d, t, f"读取标题 · {detail}"),
            cancel_cb=canceled,
        )
        report("papers_index", int(len(title_index.entries)), int(len(title_index.entries)), f"已索引 {len(title_index.entries)} 篇 PDF")

        pairs: List[Tuple[int, str, dict]] = []
        for rec in citations:
            sent = (rec.sentence or "").strip()
            for c in rec.citations:
                pairs.append((int(rec.page or 0), sent, dict(c)))
        if coverage is not None and pairs:
            scored_pairs: List[Tuple[int, int, int, str, Tuple[int, str, dict]]] = []
            for i0, (page0, sent0, cite0) in enumerate(pairs):
                a0 = (cite0.get("authors", "") or "").strip()
                y0 = (cite0.get("year", "") or "").strip()
                sk = stable_text_key(prefix="cc", page=int(page0 or 0), text=str(sent0 or ""), extra=f"{a0}|{y0}")
                seen = int(coverage.seen_count("citecheck", sk))
                page_seen = int(coverage.page_seen_count("citecheck", int(page0 or 0)))
                scored_pairs.append((0 if seen <= 0 else 1, page_seen, i0, sk, (page0, sent0, cite0)))
            scored_pairs.sort(key=lambda t: (int(t[0]), int(t[1]), int(t[2])))
            pairs = [p for _a, _b, _c, _sk, p in scored_pairs]

            # Strictly avoid repeating the same unchanged cite-check pair across iterations.
            unseen_pairs: List[Tuple[int, str, dict]] = []
            for page0, sent0, cite0 in pairs:
                a0 = (cite0.get("authors", "") or "").strip()
                y0 = (cite0.get("year", "") or "").strip()
                if not a0 or not y0:
                    continue
                sk = stable_text_key(prefix="cc", page=int(page0 or 0), text=str(sent0 or ""), extra=f"{a0}|{y0}")
                if int(coverage.seen_count("citecheck", sk)) <= 0:
                    unseen_pairs.append((page0, sent0, cite0))
            if unseen_pairs:
                pairs = unseen_pairs
            else:
                return {
                    "meta": {
                        "main_pdf": os.path.basename(main_pdf_path),
                        "papers_root": papers_root,
                        "library_pdf_root": library_pdf_root,
                        "created_at": int(time.time()),
                        "seconds": float(time.time() - t0),
                        "skipped": True,
                        "reason": "all_seen",
                    },
                    "counts": {},
                    "items": [],
                    "uncited_references": uncited_refs[:1200],
                    "citation_sentence_count": int(len(citations)),
                    "reference_count": int(len(references)),
                }

        if cfg.max_pairs and len(pairs) > int(cfg.max_pairs):
            pairs = pairs[: int(cfg.max_pairs)]

        items: List[CiteCheckItem] = []
        report("checking", 0, len(pairs), "开始核查…")

        para_cache: Dict[str, ParagraphIndex] = {}

        for idx, (page_in_main, sentence, cite) in enumerate(pairs, start=1):
            if canceled():
                break

            author = (cite.get("authors", "") or "").strip()
            year = (cite.get("year", "") or "").strip()
            if not author or not year:
                continue
            cov_key = ""
            if coverage is not None:
                cov_key = stable_text_key(prefix="cc", page=int(page_in_main or 0), text=str(sentence or ""), extra=f"{author}|{year}")

            ref = match_reference_entry(cited_author=author, cited_year=year, references=references)
            ref_missing = ref is None
            ref_title = extract_reference_title(ref.reference) if ref is not None else ""
            entry = str(ref.reference or "") if ref is not None else ""

            picked = title_index.find_by_author_year(author, year)
            if not picked and ref_title and ref is not None:
                picked = title_index.find_by_title(ref_title, ref.authors or "", threshold=cfg.title_match_threshold)

            if not picked:
                if ref_missing:
                    verdict = "REF_NOT_FOUND"
                    reason = "参考文献列表中未找到对应条目，且未在当前库中找到包含作者/年份的原文 PDF（可能 References 缺失/格式不标准）"
                else:
                    verdict = "PDF_NOT_FOUND"
                    reason = "在当前范文库 PDF 中未找到匹配的原文论文（可先导入引用原文 PDF）"
                items.append(
                    CiteCheckItem(
                        page_in_main=page_in_main,
                        original_sentence=sentence[:700],
                        cited_author=author,
                        cited_year=year,
                        ref_title=ref_title,
                        reference_entry=entry[:2000],
                        ref_missing=bool(ref_missing),
                        matched_pdf="",
                        matched_pdf_rel="",
                        verdict=verdict,
                        confidence=0.0,
                        claim="",
                        reason=reason,
                        suggested_fix="",
                        evidence=[],
                    )
                )
                if coverage is not None and cov_key:
                    coverage.mark_seen("citecheck", cov_key, page=int(page_in_main or 0), meta={"kind": "pair", "verdict": verdict})
                report("checking", idx, len(pairs), f"{verdict} · {author} ({year})")
                continue

            rel = str(picked.get("rel", "") or "").replace("\\", "/")
            full = os.path.normpath(os.path.join(papers_root, rel))
            matched_name = os.path.basename(full)

            pi = para_cache.get(full)
            if pi is None:
                pi = ParagraphIndex(cache_dir=self.cache_dir, pdf_path=full, embed_texts=self.embed_texts, model_fingerprint=self.model_fingerprint)
                report("para", 0, 0, f"抽取原文段落 · {matched_name}")
                pi.build(progress_cb=lambda d, t, detail: report("para", d, t, f"抽取原文段落 · {detail}"), cancel_cb=canceled)
                para_cache[full] = pi

            evidence = pi.search(sentence, top_k=int(cfg.paragraph_top_k or 5))
            paper_summary = ""
            try:
                pages2 = load_pdf_pages(Path(full), max_pages=2)
                head = "\n".join((pages2 or [])[:2])
                paper_summary = (head or "")[:2000]
            except Exception:
                paper_summary = ""

            verdict = "EVIDENCE_ONLY"
            confidence = 0.0
            claim = ""
            reason = ""
            suggested_fix = ""

            reason_prefix = ""
            if ref_missing:
                reason_prefix = "注意：你的 References 中未找到该条目（可能缺失/格式不标准）。以下判定基于库中疑似原文证据。"
            if cfg.use_llm and llm is not None:
                report("llm", idx, len(pairs), f"LLM 判定 · {author} ({year})")
                v = verify_citation_with_llm(
                    llm=llm,
                    citation_sentence=sentence,
                    cited_author=author,
                    cited_year=year,
                    ref_title=ref_title,
                    paper_summary=paper_summary,
                    evidence=evidence,
                    timeout_s=float(cfg.llm_timeout_s or 90.0),
                    budget=budget,
                )
                verdict = str(v.get("verdict", "") or "").strip() or verdict
                try:
                    confidence = float(v.get("confidence", 0.0) or 0.0)
                except Exception:
                    confidence = 0.0
                claim = str(v.get("claim", "") or "").strip()
                reason = str(v.get("reason", "") or "").strip()
                suggested_fix = str(v.get("suggested_fix", "") or "").strip()
                if reason_prefix:
                    reason = (reason_prefix + " " + reason).strip()
            else:
                if reason_prefix:
                    reason = reason_prefix
                else:
                    reason = "仅检索证据（未启用大模型判定）。"

            items.append(
                CiteCheckItem(
                    page_in_main=page_in_main,
                    original_sentence=sentence[:700],
                    cited_author=author,
                    cited_year=year,
                    ref_title=ref_title,
                    reference_entry=entry[:2000],
                    ref_missing=bool(ref_missing),
                    matched_pdf=matched_name,
                    matched_pdf_rel=rel,
                    verdict=verdict,
                    confidence=confidence,
                    claim=claim[:220] if claim else "",
                    reason=reason[:900] if reason else "",
                    suggested_fix=suggested_fix[:320] if suggested_fix else "",
                    evidence=list(evidence or []),
                )
            )
            if coverage is not None and cov_key:
                coverage.mark_seen("citecheck", cov_key, page=int(page_in_main or 0), meta={"kind": "pair", "verdict": verdict})

            report("checking", idx, len(pairs), f"{verdict} · {author} ({year})")

        counts: Dict[str, int] = {}
        for it in items:
            counts[it.verdict] = counts.get(it.verdict, 0) + 1

        return {
            "meta": {
                "main_pdf": os.path.basename(main_pdf_path),
                "papers_root": papers_root,
                "library_pdf_root": library_pdf_root,
                "created_at": int(time.time()),
                "seconds": float(time.time() - t0),
            },
            "counts": counts,
            "items": [it.to_dict() for it in items],
            "uncited_references": uncited_refs[:1200],
            "citation_sentence_count": int(len(citations)),
            "reference_count": int(len(references)),
        }
