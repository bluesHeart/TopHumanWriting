# -*- coding: utf-8 -*-
"""
AI Word Detector v2.7.2 - Domain Weirdness Analyzer
Compare your text against a domain corpus (PDFs) to identify unusual words/phrases
and sentence-level weirdness (domain outliers + AI-style patterns).
"""

import os
import sys
import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from collections import Counter
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict
from datetime import datetime
import time
import shutil

from version import VERSION
from i18n import get_i18n, t, set_language, get_language


# Font configuration - clean, readable fonts
FONT_UI = "Microsoft YaHei UI"  # For UI elements (supports Chinese)
FONT_MONO = "Cascadia Code"     # For code/text display (fallback to Consolas)

# Smooth corner radius
CORNER_RADIUS = 16

# N-gram separator used for storing phrase keys in JSON
NGRAM_SEP = "\t"

def _env_int(name: str, default: int, min_value: int, max_value: int) -> int:
    try:
        raw = os.environ.get(name, "").strip()
        val = int(raw) if raw else int(default)
    except Exception:
        val = int(default)
    return max(int(min_value), min(int(max_value), int(val)))


def _env_float(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        raw = os.environ.get(name, "").strip()
        val = float(raw) if raw else float(default)
    except Exception:
        val = float(default)
    return max(float(min_value), min(float(max_value), float(val)))


# Max number of corpus sentences stored for semantic retrieval
MAX_SEMANTIC_SENTENCES = _env_int("AIWORDDETECTOR_MAX_SEMANTIC_SENTENCES", 10000, 5000, 200000)
SEMANTIC_EMBED_BATCH = _env_int("AIWORDDETECTOR_SEMANTIC_EMBED_BATCH", 8, 1, 128)
SEMANTIC_PROGRESS_EVERY_S = _env_float("AIWORDDETECTOR_SEMANTIC_PROGRESS_EVERY_S", 1.0, 0.1, 10.0)

# Max number of sampled corpus sentences used to build syntax stats (UDPipe)
MAX_SYNTAX_SENTENCES = _env_int("AIWORDDETECTOR_MAX_SYNTAX_SENTENCES", 2500, 500, 20000)

# Similarity threshold for semantic outlier detection (cosine similarity)
SEMANTIC_SIM_THRESHOLD = 0.68

# Chinese stop words (minimal) for corpus building
STOP_WORDS_ZH = {
    "的", "了", "和", "与", "及", "或", "而", "但", "并", "且", "及其", "以及", "等",
    "是", "为", "在", "对", "于", "将", "被", "把", "从", "到", "由", "与其",
    "其", "该", "本", "此", "这些", "那些", "一个", "一种", "一些",
    "我们", "你们", "他们", "她们", "它们",
    "可以", "可能", "需要", "通过", "进行", "实现", "包括", "主要", "本文",
}


class CancelledError(Exception):
    """Raised when a long-running task is canceled by the user."""


class RunningStats:
    """Online mean/std calculator (Welford) for numeric streams."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def add(self, x: float):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2

    def as_dict(self) -> dict:
        if self.n < 2:
            std = 0.0
        else:
            std = (self.M2 / (self.n - 1)) ** 0.5
        return {"count": self.n, "mean": float(self.mean), "std": float(std)}

    @staticmethod
    def from_dict(d: dict) -> "RunningStats":
        rs = RunningStats()
        if not isinstance(d, dict):
            return rs
        rs.n = int(d.get("count", 0) or 0)
        rs.mean = float(d.get("mean", 0.0) or 0.0)
        std = float(d.get("std", 0.0) or 0.0)
        # approximate M2 from std when available
        if rs.n >= 2:
            rs.M2 = (std ** 2) * (rs.n - 1)
        return rs


def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_SETTINGS_DIR = None


def get_settings_dir():
    global _SETTINGS_DIR
    if _SETTINGS_DIR:
        return _SETTINGS_DIR

    env_dir = os.environ.get("AIWORDDETECTOR_DATA_DIR", "").strip()
    preferred = env_dir or os.path.join(get_app_dir(), "AIWordDetector_data")

    appdata = os.environ.get('APPDATA', os.path.expanduser('~'))
    legacy_dir = os.path.join(appdata, 'AIWordDetector')

    def _ensure_writable_dir(path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            return False
        try:
            test_path = os.path.join(path, ".__write_test__")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_path)
            return True
        except Exception:
            return False

    def _migrate_legacy(src_dir: str, dst_dir: str):
        if not src_dir or not dst_dir:
            return
        if os.path.abspath(src_dir) == os.path.abspath(dst_dir):
            return
        if not os.path.exists(src_dir):
            return

        try:
            os.makedirs(dst_dir, exist_ok=True)
        except Exception:
            return

        try:
            dst_settings = os.path.join(dst_dir, "settings.json")
            src_settings = os.path.join(src_dir, "settings.json")
            if os.path.exists(src_settings) and not os.path.exists(dst_settings):
                shutil.copy2(src_settings, dst_settings)
        except Exception:
            pass

        try:
            src_lib = os.path.join(src_dir, "libraries")
            dst_lib = os.path.join(dst_dir, "libraries")
            if os.path.exists(src_lib):
                os.makedirs(dst_lib, exist_ok=True)
                for name in os.listdir(src_lib):
                    src = os.path.join(src_lib, name)
                    dst = os.path.join(dst_lib, name)
                    if os.path.isfile(src) and not os.path.exists(dst):
                        try:
                            shutil.copy2(src, dst)
                        except Exception:
                            pass
        except Exception:
            pass

    # Prefer portable (exe directory) when writable; fallback to AppData.
    if _ensure_writable_dir(preferred):
        _migrate_legacy(legacy_dir, preferred)
        _SETTINGS_DIR = preferred
        return _SETTINGS_DIR

    os.makedirs(legacy_dir, exist_ok=True)
    _SETTINGS_DIR = legacy_dir
    return _SETTINGS_DIR


try:
    import fitz
except ImportError:
    fitz = None

try:
    import jieba
except ImportError:
    jieba = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

try:
    from tokenizers import Tokenizer
except ImportError:
    Tokenizer = None

try:
    from ufal.udpipe import Model as UDPipeModel
    from ufal.udpipe import Pipeline as UDPipePipeline
    from ufal.udpipe import ProcessingError as UDPipeProcessingError
except ImportError:
    UDPipeModel = None
    UDPipePipeline = None
    UDPipeProcessingError = None

# Extended stop words list - words to ignore in analysis
STOP_WORDS = {
    'a', 'an', 'the',
    'i', 'me', 'my', 'myself', 'we', 'our', 'ours', 'ourselves',
    'you', 'your', 'yours', 'yourself', 'yourselves',
    'he', 'him', 'his', 'himself', 'she', 'her', 'hers', 'herself',
    'it', 'its', 'itself', 'they', 'them', 'their', 'theirs', 'themselves',
    'what', 'which', 'who', 'whom', 'this', 'that', 'these', 'those',
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as',
    'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'between', 'under', 'over', 'out', 'off', 'down', 'up', 'about',
    'against', 'within', 'without', 'along', 'around', 'among',
    'and', 'or', 'but', 'nor', 'so', 'yet', 'both', 'either', 'neither',
    'not', 'only', 'than', 'when', 'while', 'if', 'then', 'else',
    'because', 'although', 'though', 'unless', 'since', 'until',
    'is', 'am', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'having', 'do', 'does', 'did', 'doing',
    'will', 'would', 'could', 'should', 'may', 'might', 'must',
    'shall', 'can', 'need', 'dare', 'ought',
    'very', 'just', 'also', 'now', 'here', 'there', 'where',
    'how', 'why', 'when', 'all', 'each', 'every', 'any', 'some',
    'no', 'more', 'most', 'other', 'such', 'own', 'same', 'too',
    'few', 'many', 'much', 'less', 'least', 'further', 'once', 'again',
    'et', 'al', 'ie', 'eg', 'cf', 'vs', 'etc', 'pp', 'vol',
    'fig', 'table', 'eq', 'eqs', 'ref', 'refs',
    'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten',
    'first', 'second', 'third',
}


EN_SECTION_HEADINGS = {
    "abstract",
    "introduction",
    "background",
    "motivation",
    "related work",
    "literature review",
    "data",
    "method",
    "methods",
    "methodology",
    "empirical strategy",
    "model",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgements",
    "acknowledgments",
    "appendix",
    "appendices",
    "keywords",
    "jel classification",
}

EN_HEADING_SMALL_WORDS = {
    "a", "an", "the",
    "and", "or", "of", "to", "in", "on", "for", "with", "via", "vs",
}

ZH_SECTION_HEADINGS = {
    "摘要",
    "中文摘要",
    "英文摘要",
    "引言",
    "前言",
    "绪论",
    "结论",
    "总结",
    "参考文献",
    "附录",
    "致谢",
    "关键词",
    "文献综述",
    "相关工作",
    "研究背景",
    "研究设计",
    "研究方法",
    "方法",
    "模型",
    "数据",
    "样本",
    "变量",
    "结果",
    "讨论",
    "实证结果",
}


def _strip_heading_prefix(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    # markdown headings / bullets
    s = re.sub(r'^\s{0,3}#{1,6}\s*', '', s)
    s = re.sub(r'^\s*[-•]\s*', '', s)
    # numbered headings like "1", "1.1", "2.3.4"
    s = re.sub(r'^\s*\d+(?:\.\d+){0,4}[.)]?\s+', '', s)
    # Chinese numbered headings like "一、" "(一)" "（一）"
    s = re.sub(r'^\s*[一二三四五六七八九十]+[、.．)]\s*', '', s)
    s = re.sub(r'^\s*[（(][一二三四五六七八九十]+[)）]\s*', '', s)
    s = re.sub(r'^\s*第[一二三四五六七八九十0-9]+[章节篇]\s*', '', s)
    return s.strip()


def is_math_like(text: str) -> bool:
    """Heuristic: detect math/equation lines so we don't treat them as prose sentences."""
    s = (text or "").strip()
    if not s:
        return False

    # Explicit LaTeX math markers
    if "\\[" in s or "\\]" in s or "$$" in s or "\\(" in s or "\\)" in s:
        return True
    if re.search(r'\\(frac|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|nu|pi|rho|sigma|tau|phi|psi|omega)\b', s, flags=re.I):
        return True
    if "\\begin{" in s or "\\end{" in s:
        return True

    compact = re.sub(r"\s+", "", s)
    if len(compact) < 8:
        return False

    # Lots of operators relative to letters is usually math.
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", compact))
    ops_set = set("=<>+-*/^_{}[]\\\\")
    ops = sum(1 for c in compact if c in ops_set)
    if ops >= 3 and (letters / max(1, len(compact))) < 0.55:
        return True

    return False


def is_heading_like(text: str, language: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False

    if is_math_like(s):
        return True

    # Markdown headings / emphasis headings
    if re.match(r'^\s{0,3}#{1,6}\s+\S+', s):
        return True
    if re.match(r'^\s*[*_]{2,}\s*\S+', s):
        return True

    # Bullet / list items
    if re.match(r'^\s*[-•]\s+\S+', s):
        return True
    if re.match(r'^\s*\d+[.)]\s+\S+', s):
        return True

    # Common heading punctuation
    if re.search(r'[:：]\s*$', s) and len(s) <= 120 and not re.search(r'[.!?。！？；;]\s*$', s):
        return True

    # Table/Figure/Section headings
    if re.match(r'^\s*(table|figure|appendix|section|chapter)\s*[:：]?\s*\d+\b', s, flags=re.I):
        return True

    # Numbered section headings like "1 Introduction" / "1.1 Data"
    if re.match(r'^\s*\d+(?:\.\d+){0,4}\s+\S+', s) and not re.search(r'[.!?。！？；;]$', s):
        return True

    if language in ('zh', 'mixed'):
        if re.match(r'^\s*(表|图)\s*\d+\b', s):
            return True
        if re.match(r'^\s*[一二三四五六七八九十]+[、.．)]\s*\S+', s):
            return True
        if re.match(r'^\s*[（(][一二三四五六七八九十]+[)）]\s*\S+', s):
            return True

        base = _strip_heading_prefix(s).strip().rstrip(':：').strip()
        base = re.sub(r'[。！？；.!?]+$', '', base).strip()
        if base in ZH_SECTION_HEADINGS:
            return True
        # "结论与展望" / "引言（续）" style
        if base and len(base) <= 12 and any(base.startswith(h) for h in ZH_SECTION_HEADINGS):
            return True

    if language in ('en', 'mixed'):
        base = _strip_heading_prefix(s)
        base_clean = re.sub(r'\s+', ' ', base).strip().rstrip(':：').strip()
        base_clean = re.sub(r'[。！？；.!?]+$', '', base_clean).strip()
        base_lower = base_clean.lower()
        if base_lower in EN_SECTION_HEADINGS:
            return True
        if base_lower and len(base_clean) <= 80:
            words = re.findall(r'[A-Za-z]+', base_clean)
            if 1 <= len(words) <= 10 and not re.search(r'[.!?]\s*$', s):
                # Avoid treating short sentences as headings
                if words[0].lower() not in {"we", "i", "this", "that", "these", "those", "it"}:
                    # ALL CAPS headings
                    letters = [c for c in base_clean if c.isalpha()]
                    if letters:
                        upper_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
                        if upper_ratio >= 0.85:
                            return True

                    # Title Case headings
                    titleish = sum(1 for w in words if w[0].isupper() or w.lower() in EN_HEADING_SMALL_WORDS) / len(words)
                    if titleish >= 0.85:
                        # Exclude common verb-heavy patterns ("X is ...")
                        if not re.search(r'\b(is|are|was|were|be|been|being|has|have|had|do|does|did|can|could|will|would|may|might|should|must)\b', base_lower):
                            return True

        # Heading prefixes like "Conclusion and Future Work"
        if base_lower:
            for h in EN_SECTION_HEADINGS:
                if base_lower.startswith(h + " "):
                    words = re.findall(r'[A-Za-z]+', base_clean)
                    if len(words) <= 10 and not re.search(r'\b(is|are|was|were|has|have|had)\b', base_lower):
                        return True

    return False


def normalize_soft_line_breaks_preserve_len(text: str) -> str:
    """Replace soft line breaks with spaces (length-preserving) to reduce false 'short sentence' alarms.

    Keeps paragraph breaks (blank lines) and structural lines (bullets/headings).
    """
    if not text or "\n" not in text:
        return text or ""

    lines = text.split("\n")
    out_parts = []

    structural_re = re.compile(
        r'^(?:'
        r'[-•]\s+|'
        r'#{1,6}\s+|'
        r'\d+[.)]\s+|'
        r'\d+(?:\.\d+){0,4}\s+\S+|'
        r'[一二三四五六七八九十]+[、.．)]\s+\S+|'
        r'[（(][一二三四五六七八九十]+[)）]\s*\S+'
        r')'
    )

    for i, line in enumerate(lines):
        out_parts.append(line)
        if i >= len(lines) - 1:
            break

        cur = line
        nxt = lines[i + 1]
        cur_s = cur.strip()
        nxt_s = nxt.strip()

        # Preserve paragraph breaks / blank lines
        if not cur_s or not nxt_s:
            out_parts.append("\n")
            continue

        cur_l = cur.lstrip()
        nxt_l = nxt.lstrip()

        # Preserve structure around headings/lists
        if structural_re.match(cur_l) or structural_re.match(nxt_l):
            out_parts.append("\n")
            continue
        if (len(cur_s) <= 80 and is_heading_like(cur_s, "mixed")) or (len(nxt_s) <= 80 and is_heading_like(nxt_s, "mixed")):
            out_parts.append("\n")
            continue

        # Otherwise treat as soft wrap
        out_parts.append(" ")

    return "".join(out_parts)


def split_sentences_with_positions(text: str, language: str) -> List[Tuple[str, int, int]]:
    """Split text into sentences with stable character positions.

    Goals:
    - Treat true sentence-ending punctuation as boundaries.
    - Treat paragraph breaks (\\n\\n+) and structural line breaks (headings/lists) as boundaries.
    - Avoid splitting on soft wraps (single \\n inside normal paragraphs).
    """
    if not text:
        return []

    lang = (language or "en").strip().lower()
    if lang not in ("en", "zh", "mixed"):
        lang = "mixed"

    end_chars = set()
    if lang in ("zh", "mixed"):
        end_chars.update("。！？；")
    if lang in ("en", "mixed"):
        end_chars.update(".!?")

    # Do not split on punctuation inside structural lines (lists/headings/captions/references).
    structural_line_re = re.compile(
        r'^\s*(?:'
        r'[-•]\s+|'                  # bullets
        r'#{1,6}\s+|'                # markdown headings
        r'\d+[.)]\s+|'               # numbered lists
        r'\d+(?:\.\d+){0,4}\s+\S+|'  # numbered headings like 1.1 Data
        r'[A-Za-z][.)]\s+|'          # lettered headings like A. Data
        r'[IVXLCDM]+[.)]\s+|'        # roman numeral headings
        r'\\\[|\\\(|\\begin\{|\$\$'  # LaTeX math blocks
        r')'
    )
    line_start = 0
    line_end = text.find("\n", 0)
    if line_end == -1:
        line_end = len(text)
    line_structural = None

    def _current_line_is_structural() -> bool:
        nonlocal line_structural
        if line_structural is not None:
            return bool(line_structural)
        line = text[line_start:line_end]
        s = (line or "").strip()
        if not s:
            line_structural = False
        else:
            try:
                line_structural = bool(structural_line_re.match((line or "").lstrip())) or bool(is_math_like(s))
            except Exception:
                line_structural = False
        return bool(line_structural)

    def _flush(start: int, end: int, out: List[Tuple[str, int, int]]):
        if end <= start:
            return
        raw = text[start:end]
        # Trim but keep original positions.
        ltrim = len(raw) - len(raw.lstrip())
        rtrim = len(raw) - len(raw.rstrip())
        s = start + ltrim
        e = end - rtrim
        if e <= s:
            return
        sent = text[s:e]
        if sent:
            out.append((sent, s, e))

    def _next_significant_char(pos: int) -> str:
        n = len(text)
        i = pos
        while i < n and text[i].isspace():
            i += 1
        # Skip common opening quotes/brackets
        while i < n and text[i] in "\"'“”‘’([{（【":
            i += 1
            while i < n and text[i].isspace():
                i += 1
        return text[i] if i < n else ""

    _EN_ABBREV = {
        "e.g",
        "i.e",
        "etc",
        "vs",
        "mr",
        "ms",
        "mrs",
        "dr",
        "prof",
        "fig",
        "eq",
        "eqs",
        "sec",
        "chap",
        "no",
        "al",  # et al.
        "jr",
        "sr",
        "u.s",
        "u.k",
    }

    def _should_split_period(dot_pos: int, after_run: int) -> bool:
        # Decimal like 3.14
        if dot_pos > 0 and after_run < len(text):
            if text[dot_pos - 1].isdigit() and text[after_run].isdigit():
                return False

        # Section/list numbering like "1." / "1.1." should not split.
        try:
            line_start = text.rfind("\n", 0, dot_pos) + 1
            prefix = text[line_start:dot_pos + 1]
            prefix_clean = re.sub(r'^[\s#*_>\-]+', '', prefix).strip()
            if re.fullmatch(r'\d+(?:\.\d+){0,4}\.', prefix_clean):
                nxt = _next_significant_char(after_run)
                if nxt:
                    return False
        except Exception:
            pass

        # Abbreviation like e.g. / i.e. where next char is a letter without space
        if after_run < len(text) and text[after_run].isalpha():
            return False

        nxt = _next_significant_char(after_run)
        if nxt and nxt.islower():
            return False

        # If previous token is a known abbreviation and the next significant char is not a clear sentence start.
        window = text[max(0, dot_pos - 32):dot_pos].lower()
        # Capture both plain words ("fig") and dotted abbreviations ("e.g", "u.s").
        m = re.search(r'([a-z](?:\.[a-z]){1,3}|[a-z]+)\s*$', window)
        prev_word = m.group(1) if m else ""
        if prev_word in _EN_ABBREV:
            # Do not split when the next token is clearly continuing the same sentence
            # (e.g., "e.g.,", "et al.)", "cf.;") which often appears in academic writing.
            if (
                (not nxt)
                or (nxt in "([{（【")
                or (nxt in ",;:)]}」』”’")
                or nxt.isdigit()
                or (nxt and nxt.islower())
            ):
                return False
        return True

    def _should_split_newline(nl_pos: int) -> bool:
        # Paragraph breaks handled separately.
        line_start = text.rfind("\n", 0, nl_pos) + 1
        cur = text[line_start:nl_pos].strip()
        # Look ahead to the next line content.
        next_nl = text.find("\n", nl_pos + 1)
        if next_nl == -1:
            next_nl = len(text)
        nxt = text[nl_pos + 1:next_nl].strip()

        if not cur or not nxt:
            return True

        # Structural breaks around headings/lists.
        if is_heading_like(cur, "mixed") or is_heading_like(nxt, "mixed"):
            return True

        # Common pattern: "We contribute as follows:" then a list.
        if cur.endswith((": ", ":\t", ":", "：")) and is_heading_like(nxt, "mixed"):
            return True

        return False

    out: List[Tuple[str, int, int]] = []
    n = len(text)
    start = 0
    i = 0
    while i < n:
        ch = text[i]
        if ch == "\n":
            j = i
            while j < n and text[j] == "\n":
                j += 1
            if j - i >= 2:
                _flush(start, i, out)
                start = j
            else:
                if _should_split_newline(i):
                    _flush(start, i, out)
                    start = i + 1
            i = j
            line_start = j
            line_end = text.find("\n", line_start)
            if line_end == -1:
                line_end = n
            line_structural = None
            continue

        if ch in end_chars:
            j = i + 1
            while j < n and text[j] in end_chars:
                j += 1

            # Do not split inside structural lines (e.g., bibliography bullets), wait for newline/paragraph.
            if _current_line_is_structural():
                i = j
                continue

            # Heuristic: period requires context to avoid splitting abbreviations/decimals.
            if ch == "." and lang in ("en", "mixed") and all(c == "." for c in text[i:j]):
                if not _should_split_period(i, j):
                    i = j
                    continue

            _flush(start, j, out)
            start = j
            i = j
            continue

        i += 1

    _flush(start, n, out)
    return out


def split_sentences(text: str, language: str) -> List[str]:
    return [s for (s, _a, _b) in split_sentences_with_positions(text, language)]


class Theme:
    """Theme configuration - supports light and dark modes"""

    # Light theme (default) - clean, bright, consistent
    LIGHT = {
        'BG_PRIMARY': "#f8f9fa",        # Very light gray background
        'BG_SECONDARY': "#ffffff",       # Pure white panels
        'BG_TERTIARY': "#e9ecef",        # Subtle gray for borders/dividers
        'BG_INPUT': "#ffffff",           # White text areas
        'BG_HOVER': "#e9ecef",           # Hover state
        'TEXT_PRIMARY': "#212529",       # Near black text
        'TEXT_SECONDARY': "#495057",     # Dark gray secondary text
        'TEXT_MUTED': "#6c757d",         # Muted gray
        'BORDER': "#dee2e6",             # Light border
    }

    # Dark theme - cohesive, easy on eyes
    DARK = {
        'BG_PRIMARY': "#1a1a1a",         # Main background
        'BG_SECONDARY': "#242424",       # Panels
        'BG_TERTIARY': "#2e2e2e",        # Borders, separators
        'BG_INPUT': "#2a2a2a",           # Text input areas
        'BG_HOVER': "#363636",           # Hover state
        'TEXT_PRIMARY': "#e4e4e4",       # Light text
        'TEXT_SECONDARY': "#a8a8a8",     # Secondary text
        'TEXT_MUTED': "#707070",         # Muted text
        'BORDER': "#3a3a3a",             # Subtle borders
    }

    # Current theme colors (will be set based on mode)
    BG_PRIMARY = LIGHT['BG_PRIMARY']
    BG_SECONDARY = LIGHT['BG_SECONDARY']
    BG_TERTIARY = LIGHT['BG_TERTIARY']
    BG_INPUT = LIGHT['BG_INPUT']
    BG_HOVER = LIGHT['BG_HOVER']
    TEXT_PRIMARY = LIGHT['TEXT_PRIMARY']
    TEXT_SECONDARY = LIGHT['TEXT_SECONDARY']
    TEXT_MUTED = LIGHT['TEXT_MUTED']
    BORDER = LIGHT['BORDER']

    # Accent colors (same for both themes)
    PRIMARY = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    PRIMARY_DARK = "#1d4ed8"

    # Status colors
    SUCCESS = "#16a34a"           # Green (darker for light theme)
    WARNING = "#d97706"           # Orange/amber
    DANGER = "#dc2626"            # Red
    NORMAL_COLOR = "#525252"      # Gray for normal

    @classmethod
    def set_mode(cls, dark_mode: bool):
        """Switch between light and dark theme"""
        theme = cls.DARK if dark_mode else cls.LIGHT
        cls.BG_PRIMARY = theme['BG_PRIMARY']
        cls.BG_SECONDARY = theme['BG_SECONDARY']
        cls.BG_TERTIARY = theme['BG_TERTIARY']
        cls.BG_INPUT = theme['BG_INPUT']
        cls.BG_HOVER = theme['BG_HOVER']
        cls.TEXT_PRIMARY = theme['TEXT_PRIMARY']
        cls.TEXT_SECONDARY = theme['TEXT_SECONDARY']
        cls.TEXT_MUTED = theme['TEXT_MUTED']
        cls.BORDER = theme['BORDER']
        # Adjust status colors for dark mode
        if dark_mode:
            cls.SUCCESS = "#22c55e"
            cls.NORMAL_COLOR = "#a3a3a3"
        else:
            cls.SUCCESS = "#16a34a"
            cls.NORMAL_COLOR = "#525252"


class LanguageDetector:
    @staticmethod
    def detect(text: str) -> str:
        if not text:
            return 'en'
        # Sampling improves performance on very large texts (e.g., full PDFs)
        sample = text[:50000] if len(text) > 50000 else text
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', sample))
        english_chars = len(re.findall(r'[a-zA-Z]', sample))
        total = chinese_chars + english_chars
        if total == 0:
            return 'en'
        chinese_ratio = chinese_chars / total
        if chinese_ratio > 0.7:
            return 'zh'
        elif chinese_ratio > 0.3:
            return 'mixed'
        return 'en'


class Settings:
    def __init__(self):
        self.settings_file = os.path.join(get_settings_dir(), 'settings.json')
        self._settings = self._load_settings()

    def _load_settings(self) -> dict:
        defaults = {'language': 'en', 'font_size': 13, 'dark_mode': False}
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    defaults.update(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
        return defaults

    def save(self):
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def get(self, key: str, default=None):
        return self._settings.get(key, default)

    def set(self, key: str, value):
        self._settings[key] = value
        self.save()


class LibraryManager:
    """Manage multiple vocabulary libraries"""
    def __init__(self):
        self.libraries_dir = os.path.join(get_settings_dir(), 'libraries')
        os.makedirs(self.libraries_dir, exist_ok=True)

    def get_library_path(self, name: str) -> str:
        """Get full path for a library file"""
        safe_name = re.sub(r'[^\w\-]', '_', name)
        return os.path.join(self.libraries_dir, f"{safe_name}.json")

    def list_libraries(self) -> list:
        """List all available libraries"""
        libraries = []
        if os.path.exists(self.libraries_dir):
            for f in os.listdir(self.libraries_dir):
                if f.endswith('.json'):
                    name = f[:-5]  # Remove .json
                    path = os.path.join(self.libraries_dir, f)
                    try:
                        with open(path, 'r', encoding='utf-8') as file:
                            data = json.load(file)
                            libraries.append({
                                'name': name,
                                'path': path,
                                'doc_count': data.get('doc_count', 0),
                                'word_count': len(data.get('word_doc_freq', {}))
                            })
                    except:
                        libraries.append({
                            'name': name,
                            'path': path,
                            'doc_count': 0,
                            'word_count': 0
                        })
        return libraries

    def create_library(self, name: str) -> str:
        """Create a new empty library, return path"""
        path = self.get_library_path(name)
        data = {
            'word_doc_freq': {},
            'word_total_freq': {},
            'doc_count': 0,
            'doc_count_by_lang': {},
            'language': 'en',
            'sentence_length_stats': {'en': {'count': 0, 'mean': 0.0, 'std': 0.0},
                                     'zh': {'count': 0, 'mean': 0.0, 'std': 0.0}},
            'bigram_doc_freq': {'en': {}, 'zh': {}},
            'bigram_total_freq': {'en': {}, 'zh': {}},
            'bigram_total_count': {'en': 0, 'zh': 0},
            'semantic': {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}},
            'total_words': 0,
            'version': '2.6'
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def delete_library(self, name: str) -> bool:
        """Delete a library"""
        path = self.get_library_path(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def rename_library(self, old_name: str, new_name: str) -> bool:
        """Rename a library"""
        old_path = self.get_library_path(old_name)
        new_path = self.get_library_path(new_name)
        if os.path.exists(old_path) and not os.path.exists(new_path):
            os.rename(old_path, new_path)
            return True
        return False

    def clear_library(self, name: str) -> bool:
        """Clear library data but keep the library"""
        path = self.get_library_path(name)
        if os.path.exists(path):
            data = {
                'word_doc_freq': {},
                'word_total_freq': {},
                'doc_count': 0,
                'doc_count_by_lang': {},
                'language': 'en',
                'sentence_length_stats': {'en': {'count': 0, 'mean': 0.0, 'std': 0.0},
                                         'zh': {'count': 0, 'mean': 0.0, 'std': 0.0}},
                'bigram_doc_freq': {'en': {}, 'zh': {}},
                'bigram_total_freq': {'en': {}, 'zh': {}},
                'bigram_total_count': {'en': 0, 'zh': 0},
                'semantic': {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}},
                'total_words': 0,
                'version': '2.6'
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            return True
        return False

    def get_library_info(self, name: str) -> dict:
        """Get detailed library info"""
        path = self.get_library_path(name)
        info = {
            'name': name,
            'path': path,
            'folder': self.libraries_dir,
            'doc_count': 0,
            'word_count': 0,
            'exists': os.path.exists(path)
        }
        if info['exists']:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    info['doc_count'] = data.get('doc_count', 0)
                    info['word_count'] = len(data.get('word_doc_freq', {}))
            except:
                pass
        return info

    def library_exists(self, name: str) -> bool:
        """Check if library exists"""
        return os.path.exists(self.get_library_path(name))


class AcademicCorpus:
    def __init__(self, library_path: str = None):
        self.word_doc_freq = Counter()
        self.word_total_freq = Counter()
        self.doc_count = 0
        self.doc_count_by_lang = Counter()
        self.total_words = 0
        self.language = 'en'  # Dominant language in corpus

        # Enhanced stats (optional, depends on corpus build version)
        self.bigram_doc_freq = {'en': Counter(), 'zh': Counter()}
        self.bigram_total_freq = {'en': Counter(), 'zh': Counter()}
        self.bigram_total_count = Counter({'en': 0, 'zh': 0})
        self.sentence_length_stats = {'en': RunningStats(), 'zh': RunningStats()}

        # Syntax stats (optional; built from a local parser like UDPipe)
        self.pos_bigram_sentence_freq = {'en': Counter(), 'zh': Counter()}
        self.pos_bigram_total_freq = {'en': Counter(), 'zh': Counter()}
        self.pos_bigram_sentence_total = Counter({'en': 0, 'zh': 0})
        self.dep_rel_total_freq = {'en': Counter(), 'zh': Counter()}
        self.dep_rel_sentence_freq = {'en': Counter(), 'zh': Counter()}
        self.syntax_meta = {
            'engine': '',
            'models': {},
            'updated_at': ''
        }

        # Semantic sentence index (stored as separate files next to library json)
        self.semantic_meta = {
            'model_id': '',
            'dim': 0,
            'sentence_count': 0,
            'updated_at': '',
            'model_fingerprint': {},
        }
        self.library_path = library_path
        self._sentence_length_baseline_cache = {}

    @staticmethod
    def _token_language(token: str) -> str:
        if re.search(r'[\u4e00-\u9fff]', token):
            return 'zh'
        if re.search(r'[a-zA-Z]', token):
            return 'en'
        return 'en'

    def _tokenize_words_en(self, text: str) -> list:
        text = text.lower()
        words = re.findall(r'\b[a-z]+\b', text)
        cleaned = []
        for w in words:
            if len(w) < 3:
                continue
            if w in STOP_WORDS:
                continue
            cleaned.append(w)
        return cleaned

    def _tokenize_words_zh(self, text: str) -> list:
        if jieba:
            tokens = jieba.lcut(text, cut_all=False)
        else:
            tokens = re.findall(r'[\u4e00-\u9fff]', text)
        cleaned = []
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if not re.fullmatch(r'[\u4e00-\u9fff]+', tok):
                continue
            if len(tok) < 2:
                continue
            if tok in STOP_WORDS_ZH:
                continue
            cleaned.append(tok)
        return cleaned

    def _tokenize_for_words(self, text: str, language: str) -> list:
        if language == 'zh':
            return self._tokenize_words_zh(text)
        if language == 'en':
            return self._tokenize_words_en(text)
        # mixed: merge both
        return self._tokenize_words_en(text) + self._tokenize_words_zh(text)

    def _tokenize_for_bigrams_en(self, text: str) -> list:
        text = text.lower()
        # Keep short function words to capture grammar patterns
        return re.findall(r'\b[a-z]+\b', text)

    def _tokenize_for_bigrams_zh(self, text: str) -> list:
        if jieba:
            tokens = jieba.lcut(text, cut_all=False)
        else:
            tokens = re.findall(r'[\u4e00-\u9fff]', text)
        cleaned = []
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if not re.fullmatch(r'[\u4e00-\u9fff]+', tok):
                continue
            # Keep 1-char tokens here (grammar/particles), but drop whitespace/punct already filtered.
            cleaned.append(tok)
        return cleaned

    def _tokenize_for_bigrams(self, text: str, language: str) -> dict:
        if language == 'en':
            return {'en': self._tokenize_for_bigrams_en(text)}
        if language == 'zh':
            return {'zh': self._tokenize_for_bigrams_zh(text)}
        return {
            'en': self._tokenize_for_bigrams_en(text),
            'zh': self._tokenize_for_bigrams_zh(text),
        }

    @staticmethod
    def _split_sentences(text: str, language: str) -> List[str]:
        return split_sentences(text, language)

    @staticmethod
    def _is_heading_like_sentence(sentence: str) -> bool:
        s = (sentence or "").strip()
        if not s:
            return False
        try:
            lang = LanguageDetector.detect(s)
        except Exception:
            lang = "mixed"
        return is_heading_like(s, lang)

    def extract_text_from_pdf(self, pdf_path: str, cancel_event=None) -> str:
        if not fitz:
            return ""

        doc = None
        try:
            doc = fitz.open(pdf_path)
            parts = []
            for page in doc:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    raise CancelledError()
                parts.append(page.get_text(flags=fitz.TEXT_DEHYPHENATE))
            return "".join(parts)
        except CancelledError:
            raise
        except Exception:
            return ""
        finally:
            try:
                if doc is not None:
                    doc.close()
            except Exception:
                pass

    def process_pdf_folder(
        self,
        folder_path: str,
        progress_callback=None,
        semantic_embedder=None,
        semantic_progress_callback=None,
        syntax_analyzer=None,
        syntax_progress_callback=None,
        cancel_event=None,
    ) -> int:
        self.word_doc_freq = Counter()
        self.word_total_freq = Counter()
        self.doc_count = 0
        self.doc_count_by_lang = Counter()
        self.total_words = 0
        self.language = 'en'
        self.bigram_doc_freq = {'en': Counter(), 'zh': Counter()}
        self.bigram_total_freq = {'en': Counter(), 'zh': Counter()}
        self.bigram_total_count = Counter({'en': 0, 'zh': 0})
        self.sentence_length_stats = {'en': RunningStats(), 'zh': RunningStats()}
        self.pos_bigram_sentence_freq = {'en': Counter(), 'zh': Counter()}
        self.pos_bigram_total_freq = {'en': Counter(), 'zh': Counter()}
        self.pos_bigram_sentence_total = Counter({'en': 0, 'zh': 0})
        self.dep_rel_total_freq = {'en': Counter(), 'zh': Counter()}
        self.dep_rel_sentence_freq = {'en': Counter(), 'zh': Counter()}
        self.syntax_meta = {'engine': '', 'models': {}, 'updated_at': ''}
        self.semantic_meta = {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}}
        self._sentence_length_baseline_cache = {}

        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            raise CancelledError()

        corpus_sentences: List[str] = []
        corpus_sentence_seen = set()
        corpus_sentences_by_lang = {'en': [], 'zh': []}
        corpus_sentence_records: List[dict] = []

        # Prepare safe temp paths for semantic index (only replace on success).
        final_paths = self.get_semantic_index_paths()
        tmp_sentences_path = ""
        tmp_embeddings_path = ""
        if final_paths and self.library_path:
            base = os.path.splitext(self.library_path)[0]
            tmp_sentences_path = f"{base}.sentences.tmp.json"
            tmp_embeddings_path = f"{base}.embeddings.tmp.npy"

        root = Path(folder_path)
        pdf_files = sorted(list(root.rglob("*.pdf")), key=lambda p: str(p).lower())
        total_files = len(pdf_files)

        for idx, pdf_file in enumerate(pdf_files):
            try:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    raise CancelledError()

                try:
                    rel = str(pdf_file.relative_to(root))
                except Exception:
                    rel = pdf_file.name

                text = self.extract_text_from_pdf(str(pdf_file), cancel_event=cancel_event)
                text = normalize_soft_line_breaks_preserve_len(text)
                doc_lang = LanguageDetector.detect(text)
                lang_flags = ['en', 'zh'] if doc_lang == 'mixed' else [doc_lang]

                counted_langs = set()
                for lang in lang_flags:
                    if lang not in ('en', 'zh'):
                        continue
                    words = self._tokenize_for_words(text, lang)
                    if words:
                        self.doc_count_by_lang[lang] += 1
                        counted_langs.add(lang)
                        unique_words_in_doc = set(words)
                        for word in unique_words_in_doc:
                            self.word_doc_freq[word] += 1
                        self.word_total_freq.update(words)
                        self.total_words += len(words)

                    # Sentence length stats (per language)
                    for sent in self._split_sentences(text, lang):
                        if lang == 'en':
                            sent_len = len(re.findall(r'\b[a-z]+\b', sent.lower()))
                        else:
                            sent_len = len(re.findall(r'[\u4e00-\u9fff]', sent))
                        if sent_len > 0:
                            self.sentence_length_stats[lang].add(float(sent_len))

                # Bigram stats
                token_streams = self._tokenize_for_bigrams(text, doc_lang)
                for lang, toks in token_streams.items():
                    if lang not in ('en', 'zh') or len(toks) < 2:
                        continue
                    if lang not in counted_langs:
                        self.doc_count_by_lang[lang] += 1
                        counted_langs.add(lang)
                    seen_in_doc = set()
                    for a, b in zip(toks, toks[1:]):
                        bg = f"{a}{NGRAM_SEP}{b}"
                        self.bigram_total_freq[lang][bg] += 1
                        self.bigram_total_count[lang] += 1
                        seen_in_doc.add(bg)
                    for bg in seen_in_doc:
                        self.bigram_doc_freq[lang][bg] += 1

                if counted_langs:
                    self.doc_count += 1

                # Collect sentences for semantic index (language-agnostic)
                if semantic_embedder is not None and len(corpus_sentences) < MAX_SEMANTIC_SENTENCES:
                    for sent in self._split_sentences(text, doc_lang):
                        clean = re.sub(r'\s+', ' ', sent).strip()
                        if not clean:
                            continue
                        if self._is_heading_like_sentence(clean):
                            continue

                        lang_guess = LanguageDetector.detect(clean)
                        if lang_guess == 'en':
                            if len(re.findall(r'\b[a-z]+\b', clean.lower())) < 6:
                                continue
                            norm = clean.lower()
                        elif lang_guess == 'zh':
                            if len(re.findall(r'[\u4e00-\u9fff]', clean)) < 12:
                                continue
                            norm = clean
                        else:
                            if len(clean) < 24:
                                continue
                            norm = clean

                        if norm in corpus_sentence_seen:
                            continue
                        corpus_sentence_seen.add(norm)
                        corpus_sentences.append(clean)
                        corpus_sentence_records.append({
                            "text": clean,
                            "source": {"pdf": rel},
                        })
                        if lang_guess in ('en', 'zh') and len(corpus_sentences_by_lang.get(lang_guess, [])) < int(MAX_SYNTAX_SENTENCES or 0):
                            corpus_sentences_by_lang[lang_guess].append(clean)
                        if len(corpus_sentences) >= MAX_SEMANTIC_SENTENCES:
                            break

                if progress_callback:
                    progress_callback(idx + 1, total_files, rel)
            except CancelledError:
                raise
            except Exception:
                pass

        # Determine dominant corpus language
        if self.doc_count_by_lang:
            self.language = 'en' if self.doc_count_by_lang.get('en', 0) >= self.doc_count_by_lang.get('zh', 0) else 'zh'
        else:
            self.language = 'en'

        # Build semantic index files
        if semantic_embedder is not None and self.library_path and corpus_sentences and np is not None:
            try:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    raise CancelledError()

                if semantic_progress_callback:
                    semantic_progress_callback(0, len(corpus_sentences), "Embedding")
                def _report(done, total):
                    if semantic_progress_callback:
                        semantic_progress_callback(done, total, "Embedding")
                embeddings = semantic_embedder.embed(
                    corpus_sentences,
                    batch_size=SEMANTIC_EMBED_BATCH,
                    progress_callback=_report,
                    progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                    cancel_event=cancel_event,
                )
                if final_paths and tmp_sentences_path and tmp_embeddings_path:
                    # Write to temp, then atomically replace final files.
                    with open(tmp_sentences_path, "w", encoding="utf-8") as f:
                        to_dump = corpus_sentence_records if corpus_sentence_records else corpus_sentences
                        json.dump(to_dump, f, ensure_ascii=False)
                    np.save(tmp_embeddings_path, embeddings.astype(np.float32, copy=False))
                    os.replace(tmp_sentences_path, final_paths['sentences'])
                    os.replace(tmp_embeddings_path, final_paths['embeddings'])

                self.semantic_meta = {
                    'model_id': getattr(semantic_embedder, "model_id", "") or "",
                    'dim': int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 else 0,
                    'sentence_count': int(len(corpus_sentences)),
                    'updated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'model_fingerprint': getattr(semantic_embedder, "model_fingerprint", lambda: {})(),
                }
                if semantic_progress_callback:
                    semantic_progress_callback(len(corpus_sentences), len(corpus_sentences), "Embedding")
            except CancelledError:
                # Best-effort cleanup of temp files; keep existing index intact.
                for p in (tmp_sentences_path, tmp_embeddings_path):
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                raise
            except Exception:
                for p in (tmp_sentences_path, tmp_embeddings_path):
                    try:
                        if p and os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
                self.semantic_meta = {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}}

        # Build syntax stats (POS bigrams + dependency relations) from the sampled corpus sentences.
        if syntax_analyzer is not None and self.library_path and isinstance(corpus_sentences_by_lang, dict):
            try:
                if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                    raise CancelledError()

                langs = [l for l in ("en", "zh") if corpus_sentences_by_lang.get(l)]
                langs = [l for l in langs if getattr(syntax_analyzer, "has_lang", lambda _l: False)(l)]
                total_sents = sum(len(corpus_sentences_by_lang.get(l, [])) for l in langs)

                if syntax_progress_callback and total_sents > 0:
                    syntax_progress_callback(0, total_sents, "Syntax")

                done = 0
                last_report_t = time.monotonic()
                last_report_done = 0
                models_meta = {}
                try:
                    model_dir = getattr(syntax_analyzer, "model_dir", "") or ""
                    for l in ("en", "zh"):
                        p = os.path.join(model_dir, f"{l}.udpipe")
                        if p and os.path.exists(p):
                            models_meta[l] = os.path.basename(p)
                except Exception:
                    models_meta = {}

                for lang in langs:
                    parsed_count = 0
                    for sent in corpus_sentences_by_lang.get(lang, []):
                        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                            raise CancelledError()

                        res = None
                        try:
                            res = syntax_analyzer.analyze_sentence(sent, lang)
                        except Exception:
                            res = None

                        if res and isinstance(res, dict):
                            upos = list(res.get("upos", []) or [])
                            deprel = list(res.get("deprel", []) or [])

                            if len(upos) >= 2:
                                seen_bg = set()
                                for a, b in zip(upos, upos[1:]):
                                    if not a or not b:
                                        continue
                                    key = f"{a}{NGRAM_SEP}{b}"
                                    self.pos_bigram_total_freq[lang][key] += 1
                                    seen_bg.add(key)
                                for key in seen_bg:
                                    self.pos_bigram_sentence_freq[lang][key] += 1

                            seen_rel = set()
                            for r in deprel:
                                r = (r or "").strip()
                                if not r:
                                    continue
                                self.dep_rel_total_freq[lang][r] += 1
                                seen_rel.add(r)
                            for r in seen_rel:
                                self.dep_rel_sentence_freq[lang][r] += 1

                            parsed_count += 1

                        done += 1
                        if syntax_progress_callback:
                            now = time.monotonic()
                            if done >= total_sents or (now - last_report_t) >= 1.0 or (done - last_report_done) >= 50:
                                last_report_t = now
                                last_report_done = done
                                syntax_progress_callback(done, total_sents, "Syntax")

                    self.pos_bigram_sentence_total[lang] = int(parsed_count)

                self.syntax_meta = {
                    'engine': 'udpipe',
                    'models': models_meta,
                    'updated_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if syntax_progress_callback and total_sents > 0:
                    syntax_progress_callback(total_sents, total_sents, "Syntax")
            except CancelledError:
                raise
            except Exception:
                self.pos_bigram_sentence_total = Counter({'en': 0, 'zh': 0})
                self.syntax_meta = {'engine': '', 'models': {}, 'updated_at': ''}

        return self.doc_count

    def get_word_stats(self, word: str) -> dict:
        word = word.strip().lower()
        token_lang = self._token_language(word)
        docs_total = self.doc_count_by_lang.get(token_lang, 0) or self.doc_count
        doc_freq = self.word_doc_freq.get(word, 0)
        total_freq = self.word_total_freq.get(word, 0)
        return {
            'word': word,
            'doc_freq': doc_freq,
            'doc_percent': (doc_freq / docs_total * 100) if docs_total > 0 else 0,
            'total_freq': total_freq,
            'docs_total': docs_total
        }

    def classify_word(self, word: str) -> str:
        stats = self.get_word_stats(word)
        pct = stats['doc_percent']
        if pct == 0:
            return 'unseen'
        elif pct < 10:
            return 'rare'
        elif pct < 50:
            return 'normal'
        else:
            return 'common'

    def save_vocabulary(self, filepath=None):
        if filepath is None:
            filepath = self.library_path
        if not filepath:
            return
        data = {
            'word_doc_freq': dict(self.word_doc_freq),
            'word_total_freq': dict(self.word_total_freq),
            'doc_count': self.doc_count,
            'doc_count_by_lang': dict(self.doc_count_by_lang),
            'language': self.language,
            'sentence_length_stats': {
                'en': self.sentence_length_stats['en'].as_dict(),
                'zh': self.sentence_length_stats['zh'].as_dict(),
            },
            'bigram_doc_freq': {
                'en': dict(self.bigram_doc_freq['en']),
                'zh': dict(self.bigram_doc_freq['zh']),
            },
            'bigram_total_freq': {
                'en': dict(self.bigram_total_freq['en']),
                'zh': dict(self.bigram_total_freq['zh']),
            },
            'bigram_total_count': {
                'en': int(self.bigram_total_count.get('en', 0)),
                'zh': int(self.bigram_total_count.get('zh', 0)),
            },
            'syntax': {
                'meta': dict(self.syntax_meta),
                'pos_bigram_sentence_freq': {
                    'en': dict(self.pos_bigram_sentence_freq.get('en', Counter())),
                    'zh': dict(self.pos_bigram_sentence_freq.get('zh', Counter())),
                },
                'pos_bigram_total_freq': {
                    'en': dict(self.pos_bigram_total_freq.get('en', Counter())),
                    'zh': dict(self.pos_bigram_total_freq.get('zh', Counter())),
                },
                'pos_bigram_sentence_total': {
                    'en': int(self.pos_bigram_sentence_total.get('en', 0)),
                    'zh': int(self.pos_bigram_sentence_total.get('zh', 0)),
                },
                'dep_rel_total_freq': {
                    'en': dict(self.dep_rel_total_freq.get('en', Counter())),
                    'zh': dict(self.dep_rel_total_freq.get('zh', Counter())),
                },
                'dep_rel_sentence_freq': {
                    'en': dict(self.dep_rel_sentence_freq.get('en', Counter())),
                    'zh': dict(self.dep_rel_sentence_freq.get('zh', Counter())),
                },
            },
            'semantic': dict(self.semantic_meta),
            'total_words': self.total_words,
            'version': '2.6'
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)

    def load_vocabulary(self, filepath=None) -> bool:
        if filepath is None:
            filepath = self.library_path
        if not filepath or not os.path.exists(filepath):
            return False
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if 'word_freq' in data and 'word_doc_freq' not in data:
                self.word_total_freq = Counter(data.get('word_freq', {}))
                self.doc_count = data.get('pdf_count', 0)
                self.word_doc_freq = Counter()
                for word, freq in self.word_total_freq.items():
                    estimated_docs = min(self.doc_count, max(1, freq // 10))
                    self.word_doc_freq[word] = estimated_docs
                self.total_words = data.get('total_words', 0)
                self.doc_count_by_lang = Counter({'en': int(self.doc_count)})
                self.language = data.get('language', 'en') or 'en'
                self.bigram_doc_freq = {'en': Counter(), 'zh': Counter()}
                self.bigram_total_freq = {'en': Counter(), 'zh': Counter()}
                self.bigram_total_count = Counter({'en': 0, 'zh': 0})
                self.sentence_length_stats = {'en': RunningStats(), 'zh': RunningStats()}
                self.pos_bigram_sentence_freq = {'en': Counter(), 'zh': Counter()}
                self.pos_bigram_total_freq = {'en': Counter(), 'zh': Counter()}
                self.pos_bigram_sentence_total = Counter({'en': 0, 'zh': 0})
                self.dep_rel_total_freq = {'en': Counter(), 'zh': Counter()}
                self.dep_rel_sentence_freq = {'en': Counter(), 'zh': Counter()}
                self.syntax_meta = {'engine': '', 'models': {}, 'updated_at': ''}
                self.semantic_meta = {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}}
            else:
                self.word_doc_freq = Counter(data.get('word_doc_freq', {}))
                self.word_total_freq = Counter(data.get('word_total_freq', {}))
                self.doc_count = data.get('doc_count', 0)
                self.total_words = data.get('total_words', 0)
                self.doc_count_by_lang = Counter(data.get('doc_count_by_lang', {}))
                if not self.doc_count_by_lang and self.doc_count:
                    self.doc_count_by_lang = Counter({'en': int(self.doc_count)})
                self.language = data.get('language', 'en') or 'en'

                sls = data.get('sentence_length_stats', {})
                self.sentence_length_stats = {
                    'en': RunningStats.from_dict(sls.get('en', {})),
                    'zh': RunningStats.from_dict(sls.get('zh', {})),
                }

                bdf = data.get('bigram_doc_freq', {})
                btf = data.get('bigram_total_freq', {})
                btc = data.get('bigram_total_count', {})
                self.bigram_doc_freq = {
                    'en': Counter((bdf.get('en') if isinstance(bdf, dict) else {}) or {}),
                    'zh': Counter((bdf.get('zh') if isinstance(bdf, dict) else {}) or {}),
                }
                self.bigram_total_freq = {
                    'en': Counter((btf.get('en') if isinstance(btf, dict) else {}) or {}),
                    'zh': Counter((btf.get('zh') if isinstance(btf, dict) else {}) or {}),
                }
                self.bigram_total_count = Counter({
                    'en': int((btc.get('en') if isinstance(btc, dict) else 0) or 0),
                    'zh': int((btc.get('zh') if isinstance(btc, dict) else 0) or 0),
                })

                self.semantic_meta = data.get('semantic', {}) if isinstance(data.get('semantic', {}), dict) else {}
                if not self.semantic_meta:
                    self.semantic_meta = {'model_id': '', 'dim': 0, 'sentence_count': 0, 'updated_at': '', 'model_fingerprint': {}}
                if 'model_fingerprint' not in self.semantic_meta:
                    self.semantic_meta['model_fingerprint'] = {}

                syn = data.get('syntax', {}) if isinstance(data.get('syntax', {}), dict) else {}
                meta = syn.get('meta', {}) if isinstance(syn.get('meta', {}), dict) else {}
                self.syntax_meta = {
                    'engine': meta.get('engine', '') or '',
                    'models': meta.get('models', {}) if isinstance(meta.get('models', {}), dict) else {},
                    'updated_at': meta.get('updated_at', '') or '',
                }
                psf = syn.get('pos_bigram_sentence_freq', {}) if isinstance(syn.get('pos_bigram_sentence_freq', {}), dict) else {}
                ptf = syn.get('pos_bigram_total_freq', {}) if isinstance(syn.get('pos_bigram_total_freq', {}), dict) else {}
                pst = syn.get('pos_bigram_sentence_total', {}) if isinstance(syn.get('pos_bigram_sentence_total', {}), dict) else {}
                drt = syn.get('dep_rel_total_freq', {}) if isinstance(syn.get('dep_rel_total_freq', {}), dict) else {}
                drs = syn.get('dep_rel_sentence_freq', {}) if isinstance(syn.get('dep_rel_sentence_freq', {}), dict) else {}

                self.pos_bigram_sentence_freq = {
                    'en': Counter((psf.get('en') if isinstance(psf, dict) else {}) or {}),
                    'zh': Counter((psf.get('zh') if isinstance(psf, dict) else {}) or {}),
                }
                self.pos_bigram_total_freq = {
                    'en': Counter((ptf.get('en') if isinstance(ptf, dict) else {}) or {}),
                    'zh': Counter((ptf.get('zh') if isinstance(ptf, dict) else {}) or {}),
                }
                self.pos_bigram_sentence_total = Counter({
                    'en': int((pst.get('en') if isinstance(pst, dict) else 0) or 0),
                    'zh': int((pst.get('zh') if isinstance(pst, dict) else 0) or 0),
                })
                self.dep_rel_total_freq = {
                    'en': Counter((drt.get('en') if isinstance(drt, dict) else {}) or {}),
                    'zh': Counter((drt.get('zh') if isinstance(drt, dict) else {}) or {}),
                }
                self.dep_rel_sentence_freq = {
                    'en': Counter((drs.get('en') if isinstance(drs, dict) else {}) or {}),
                    'zh': Counter((drs.get('zh') if isinstance(drs, dict) else {}) or {}),
                }
            self.library_path = filepath
            self._sentence_length_baseline_cache = {}
            return True
        except Exception:
            return False

    def get_common_words(self, top_n=300) -> list:
        return self.word_doc_freq.most_common(top_n)

    def get_sentence_length_baseline(self, language: str = None) -> dict:
        lang = (language or self.language or "en").strip().lower()
        if lang not in ("en", "zh"):
            lang = self.language if (self.language in ("en", "zh")) else "en"

        cached = self._sentence_length_baseline_cache.get(lang)
        if isinstance(cached, dict) and cached.get("count", 0):
            return cached

        baseline = None

        # Prefer the semantic sentence sample for a robust baseline:
        # sentence_length_stats may be skewed by PDF layout artifacts (line breaks, tables, headers).
        try:
            if self.semantic_index_exists():
                paths = self.get_semantic_index_paths()
                s_path = paths.get("sentences", "")
                if s_path and os.path.exists(s_path):
                    with open(s_path, "r", encoding="utf-8") as f:
                        sents = json.load(f)
                    if isinstance(sents, list) and sents:
                        rs = RunningStats()
                        lengths: List[int] = []
                        for s in sents:
                            text = ""
                            if isinstance(s, str):
                                text = s
                            elif isinstance(s, dict):
                                v = s.get("text", None)
                                if not isinstance(v, str):
                                    v = s.get("sentence", "")
                                text = v if isinstance(v, str) else ""

                            if not isinstance(text, str) or not text:
                                continue
                            if lang == "zh":
                                l = len(re.findall(r"[\u4e00-\u9fff]", text))
                            else:
                                l = len(re.findall(r"\b[a-z]+\b", text.lower()))
                            if l <= 0:
                                continue
                            lengths.append(int(l))
                            rs.add(float(l))

                        if len(lengths) >= 50:
                            lengths.sort()
                            n = len(lengths)

                            def _pct(p: float) -> int:
                                if n <= 0:
                                    return 0
                                idx = int(round((n - 1) * p))
                                idx = max(0, min(n - 1, idx))
                                return int(lengths[idx])

                            baseline = rs.as_dict()
                            baseline.update({
                                "p50": _pct(0.50),
                                "p90": _pct(0.90),
                                "p95": _pct(0.95),
                                "source": "semantic_index",
                            })
        except Exception:
            baseline = None

        if baseline is None:
            rs = self.sentence_length_stats.get(lang)
            if not rs:
                baseline = {"count": 0, "mean": 0.0, "std": 0.0, "source": "default"}
            else:
                baseline = rs.as_dict()
                baseline["source"] = "corpus_stats"

        self._sentence_length_baseline_cache[lang] = baseline
        return baseline

    def has_phrase_stats(self, language: str = None) -> bool:
        lang = language or self.language
        return bool(self.bigram_total_count.get(lang, 0)) and bool(self.bigram_doc_freq.get(lang, Counter()))

    def get_bigram_stats(self, bigram_key: str, language: str = None) -> dict:
        lang = language or self.language
        docs_total = self.doc_count_by_lang.get(lang, 0) or self.doc_count
        doc_freq = self.bigram_doc_freq.get(lang, Counter()).get(bigram_key, 0)
        total_freq = self.bigram_total_freq.get(lang, Counter()).get(bigram_key, 0)
        return {
            'bigram': bigram_key.replace(NGRAM_SEP, ' '),
            'key': bigram_key,
            'doc_freq': doc_freq,
            'doc_percent': (doc_freq / docs_total * 100) if docs_total > 0 else 0,
            'total_freq': total_freq,
            'docs_total': docs_total
        }

    def classify_bigram(self, bigram_key: str, language: str = None) -> str:
        stats = self.get_bigram_stats(bigram_key, language=language)
        pct = stats['doc_percent']
        if pct == 0:
            return 'unseen'
        elif pct < 10:
            return 'rare'
        elif pct < 50:
            return 'normal'
        else:
            return 'common'

    def has_syntax_stats(self, language: str = None) -> bool:
        lang = language or self.language
        try:
            lang = (lang or "").strip().lower()
        except Exception:
            lang = "en"
        if lang not in ("en", "zh"):
            lang = "en"
        return bool(int(self.pos_bigram_sentence_total.get(lang, 0) or 0)) and bool(self.pos_bigram_sentence_freq.get(lang, Counter()))

    def get_pos_bigram_stats(self, pos_bigram_key: str, language: str = None) -> dict:
        lang = language or self.language
        try:
            lang = (lang or "").strip().lower()
        except Exception:
            lang = "en"
        if lang not in ("en", "zh"):
            lang = "en"
        total_sents = int(self.pos_bigram_sentence_total.get(lang, 0) or 0)
        sent_freq = int(self.pos_bigram_sentence_freq.get(lang, Counter()).get(pos_bigram_key, 0) or 0)
        total_freq = int(self.pos_bigram_total_freq.get(lang, Counter()).get(pos_bigram_key, 0) or 0)
        return {
            'pos_bigram': pos_bigram_key.replace(NGRAM_SEP, '→'),
            'key': pos_bigram_key,
            'sent_freq': sent_freq,
            'sent_percent': (sent_freq / total_sents * 100) if total_sents > 0 else 0.0,
            'total_freq': total_freq,
            'sent_total': total_sents,
        }

    def get_semantic_index_paths(self, filepath: str = None) -> dict:
        lib_path = filepath or self.library_path
        if not lib_path:
            return {}
        base = os.path.splitext(lib_path)[0]
        return {
            'sentences': f"{base}.sentences.json",
            'embeddings': f"{base}.embeddings.npy",
        }

    def semantic_index_exists(self) -> bool:
        paths = self.get_semantic_index_paths()
        return bool(paths) and os.path.exists(paths.get('sentences', '')) and os.path.exists(paths.get('embeddings', ''))


class SemanticEmbedder:
    """Multilingual sentence embedder backed by a local ONNX model."""

    def __init__(self, model_dir: str, model_id: str = ""):
        missing = []
        if np is None:
            missing.append("numpy")
        if ort is None:
            missing.append("onnxruntime")
        if Tokenizer is None:
            missing.append("tokenizers")
        if missing:
            raise RuntimeError("Missing dependencies: " + "/".join(missing))

        self.model_dir = model_dir
        self.model_id = model_id or os.path.basename(os.path.normpath(model_dir))

        tok_path = os.path.join(model_dir, "tokenizer.json")
        if not os.path.exists(tok_path):
            raise FileNotFoundError("tokenizer.json not found in model directory")
        self.tokenizer_path = tok_path

        self.tokenizer = Tokenizer.from_file(tok_path)

        # Determine max length from config when available (cap to 256 for speed).
        self.max_length = 256
        for cfg_name, key in (("tokenizer_config.json", "model_max_length"), ("config.json", "max_position_embeddings")):
            try:
                cfg_path = os.path.join(model_dir, cfg_name)
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    val = cfg.get(key, None)
                    if isinstance(val, int) and 0 < val < 100000:
                        self.max_length = min(self.max_length, int(val))
            except Exception:
                pass

        # Pad token id (prefer tokenizer vocab; fall back to config.json).
        self.pad_id = None
        try:
            self.pad_id = self.tokenizer.token_to_id("<pad>")
        except Exception:
            self.pad_id = None
        if self.pad_id is None:
            try:
                cfg_path = os.path.join(model_dir, "config.json")
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.pad_id = int(cfg.get("pad_token_id", 0) or 0)
            except Exception:
                self.pad_id = 0
        self.pad_id = int(self.pad_id or 0)

        try:
            self.tokenizer.enable_truncation(max_length=int(self.max_length))
        except Exception:
            pass

        onnx_candidates = [
            os.path.join(model_dir, "model.onnx"),
            os.path.join(model_dir, "onnx", "model.onnx"),
        ]
        onnx_path = next((p for p in onnx_candidates if os.path.exists(p)), None)
        if not onnx_path:
            raise FileNotFoundError("model.onnx not found in model directory")
        self.onnx_path = onnx_path

        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.input_names = {i.name for i in self.session.get_inputs()}

    def model_fingerprint(self) -> dict:
        """Best-effort lightweight fingerprint to detect model changes without hashing huge files."""
        def stat(path: str) -> dict:
            try:
                if not path or not os.path.exists(path):
                    return {"exists": False}
                return {
                    "exists": True,
                    "size": int(os.path.getsize(path)),
                    "mtime": int(os.path.getmtime(path)),
                    "name": os.path.basename(path),
                }
            except Exception:
                return {"exists": False}

        return {
            "model_id": getattr(self, "model_id", "") or "",
            "onnx": stat(getattr(self, "onnx_path", "") or ""),
            "tokenizer": stat(getattr(self, "tokenizer_path", "") or ""),
        }

    @staticmethod
    def _l2_normalize(x):
        denom = np.linalg.norm(x, axis=1, keepdims=True)
        denom = np.clip(denom, 1e-12, None)
        return x / denom

    def _bucket_seq_len(self, seq_len: int) -> int:
        """Reduce ONNX dynamic-shape overhead by bucketing sequence lengths.

        Using many distinct sequence lengths can be significantly slower on first run.
        """
        n = max(1, int(seq_len or 1))
        # Round up to a multiple of 16 (capped by max_length).
        bucket = ((n + 15) // 16) * 16
        return int(min(int(self.max_length), int(bucket)))

    def embed(
        self,
        texts: List[str],
        batch_size: int = 32,
        progress_callback=None,
        progress_every_s: float = 0.25,
        cancel_event=None,
    ) -> "np.ndarray":
        if not texts:
            return np.zeros((0, 1), dtype=np.float32)

        total = len(texts)
        processed = 0
        last_report = 0.0

        vectors = []
        for start in range(0, len(texts), batch_size):
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                raise CancelledError()

            batch = [t if isinstance(t, str) else str(t) for t in texts[start:start + batch_size]]
            encodings = self.tokenizer.encode_batch(batch)
            actual_max_len = max((len(e.ids) for e in encodings), default=1)
            max_len = self._bucket_seq_len(actual_max_len)

            input_ids = np.full((len(encodings), max_len), self.pad_id, dtype=np.int64)
            attention_mask = np.zeros((len(encodings), max_len), dtype=np.int64)
            token_type_ids = np.zeros((len(encodings), max_len), dtype=np.int64)

            for i, e in enumerate(encodings):
                ids = list(getattr(e, "ids", []) or [])
                mask = list(getattr(e, "attention_mask", []) or [])
                types = list(getattr(e, "type_ids", []) or [])
                if not mask:
                    mask = [1] * len(ids)
                if not types:
                    types = [0] * len(ids)
                n = min(max_len, len(ids))
                if n <= 0:
                    continue
                input_ids[i, :n] = np.asarray(ids[:n], dtype=np.int64)
                attention_mask[i, :n] = np.asarray(mask[:n], dtype=np.int64)
                token_type_ids[i, :n] = np.asarray(types[:n], dtype=np.int64)

            inputs = {}
            if "input_ids" in self.input_names:
                inputs["input_ids"] = input_ids
            if "attention_mask" in self.input_names:
                inputs["attention_mask"] = attention_mask
            if "token_type_ids" in self.input_names:
                inputs["token_type_ids"] = token_type_ids
            outputs = self.session.run(None, inputs)
            if not outputs:
                raise RuntimeError("ONNX session returned no outputs")
            out = outputs[0]

            # If the model already returns pooled embeddings: (batch, dim)
            if getattr(out, "ndim", 0) == 2:
                emb = out.astype(np.float32, copy=False)
            else:
                # Otherwise mean-pool last hidden state: (batch, seq, hidden)
                mask = attention_mask.astype(np.float32)[:, :, None]
                summed = (out.astype(np.float32) * mask).sum(axis=1)
                denom = np.clip(mask.sum(axis=1), 1e-6, None)
                emb = (summed / denom).astype(np.float32, copy=False)

            vectors.append(self._l2_normalize(emb))

            processed = min(total, start + len(batch))
            if progress_callback:
                now = time.monotonic()
                if processed >= total or (now - last_report) >= float(progress_every_s or 0.0):
                    last_report = now
                    try:
                        progress_callback(processed, total)
                    except Exception:
                        pass

        return np.vstack(vectors)


class SemanticSentenceIndex:
    """In-memory sentence embedding index for cosine similarity retrieval."""

    def __init__(self, sentences: List[str], embeddings: "np.ndarray", sources: Optional[List[dict]] = None):
        self.sentences = sentences
        self.embeddings = embeddings  # (n, d), float32, L2-normalized
        if sources is None or not isinstance(sources, list) or len(sources) != len(sentences):
            sources = [{} for _ in range(len(sentences))]
        self.sources = sources

    @classmethod
    def load(cls, sentences_path: str, embeddings_path: str) -> "SemanticSentenceIndex":
        if np is None:
            raise RuntimeError("Missing dependency: numpy")
        with open(sentences_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        sentences: List[str] = []
        sources: List[dict] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    sentences.append(item)
                    sources.append({})
                    continue
                if isinstance(item, dict):
                    text = item.get("text", None)
                    if not isinstance(text, str):
                        text = item.get("sentence", "")
                    if not isinstance(text, str):
                        text = ""
                    src = item.get("source", {}) if isinstance(item.get("source", {}), dict) else {}
                    if not src:
                        # Back-compat: allow {"pdf": "..."} or {"path": "..."} at top-level.
                        pdf = item.get("pdf", "") or item.get("path", "") or item.get("file", "")
                        if isinstance(pdf, str) and pdf.strip():
                            src = {"pdf": pdf.strip()}
                    sentences.append(text)
                    sources.append(src if isinstance(src, dict) else {})
                    continue
                # Unknown item type; keep alignment.
                sentences.append("")
                sources.append({})
        else:
            sentences = []
            sources = []
        embeddings = np.load(embeddings_path, mmap_mode="r")
        return cls(sentences=sentences, embeddings=embeddings, sources=sources)

    def query_topk(self, query_vec: "np.ndarray", top_k: int = 3) -> List[Tuple[float, int]]:
        if query_vec.ndim != 1:
            query_vec = query_vec.reshape(-1)
        sims = self.embeddings @ query_vec.astype(np.float32, copy=False)
        k = max(1, min(int(top_k), int(sims.shape[0])))
        idx = np.argpartition(sims, -k)[-k:]
        idx = idx[np.argsort(sims[idx])[::-1]]
        return [(float(sims[i]), int(i)) for i in idx]

    def get_sentence(self, idx: int) -> str:
        if 0 <= idx < len(self.sentences):
            return self.sentences[idx]
        return ""

    def get_source(self, idx: int) -> dict:
        try:
            if 0 <= idx < len(self.sources):
                src = self.sources[idx]
                return src if isinstance(src, dict) else {}
        except Exception:
            pass
        return {}


class UDPipeSyntaxAnalyzer:
    """Lightweight POS/Dependency analyzer using local UDPipe models."""

    def __init__(self, model_dir: str):
        if UDPipeModel is None or UDPipePipeline is None or UDPipeProcessingError is None:
            raise RuntimeError("Missing dependency: ufal.udpipe")
        self.model_dir = model_dir
        self._models = {}     # lang -> UDPipeModel
        self._pipelines = {}  # lang -> UDPipePipeline

    def _model_path(self, lang: str) -> str:
        return os.path.join(self.model_dir, f"{lang}.udpipe")

    def has_lang(self, lang: str) -> bool:
        lang = (lang or "").strip().lower()
        if lang not in ("en", "zh"):
            return False
        return os.path.exists(self._model_path(lang))

    def _get_pipeline(self, lang: str):
        lang = (lang or "").strip().lower()
        if lang not in ("en", "zh"):
            raise ValueError("Unsupported language for UDPipeSyntaxAnalyzer")
        if lang in self._pipelines:
            return self._pipelines[lang]

        model_path = self._model_path(lang)
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"UDPipe model not found: {model_path}")
        model = UDPipeModel.load(model_path)
        if model is None:
            raise RuntimeError(f"Failed to load UDPipe model: {model_path}")
        pipeline = UDPipePipeline(model, "tokenize", UDPipePipeline.DEFAULT, UDPipePipeline.DEFAULT, "conllu")
        self._models[lang] = model
        self._pipelines[lang] = pipeline
        return pipeline

    @staticmethod
    def _parse_conllu_tokens(conllu: str) -> Tuple[List[str], List[str]]:
        upos: List[str] = []
        deprel: List[str] = []
        for line in (conllu or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 8:
                continue
            tok_id = parts[0]
            # Skip multi-word tokens and empty nodes.
            if "-" in tok_id or "." in tok_id:
                continue
            upos.append(parts[3] or "")
            deprel.append(parts[7] or "")
        return upos, deprel

    def analyze_sentence(self, sentence: str, lang: str) -> Optional[dict]:
        s = (sentence or "").strip()
        if not s:
            return None
        pipeline = self._get_pipeline(lang)
        err = UDPipeProcessingError()
        conllu = pipeline.process(s, err)
        if err.occurred():
            return None
        upos, deprel = self._parse_conllu_tokens(conllu)
        if not upos:
            return None
        return {"upos": upos, "deprel": deprel}

@dataclass
class SentenceIssue:
    """Represents a single issue found in a sentence"""
    issue_type: str       # "long_sentence" | "ai_transition" | "ai_word" | "passive" | "redundancy" | "template"
    description: str      # Human-readable description
    severity: str         # "warning" | "info"
    matched_text: str = ""  # The specific text that triggered the issue
    span: Tuple[int, int] = (0, 0)  # Position in sentence (optional)


@dataclass
class SentenceDiagnosis:
    """Diagnosis result for a single sentence"""
    index: int                          # Sentence index (0-based)
    text: str                           # Original sentence text
    start_pos: int                      # Start position in full text
    end_pos: int                        # End position in full text
    issues: List[SentenceIssue] = field(default_factory=list)


class StyleAnalyzer:
    """Sentence-level style analyzer for detecting AI writing patterns"""

    def __init__(self, ai_words_data: dict = None, language: str = 'zh'):
        self.language = language
        self.ai_words_data = ai_words_data or {}

        # Load AI transitions data
        self.transitions_data = self._load_transitions()

        # AI transition words (flat lists for quick lookup)
        self.ai_transitions_zh = set(self.transitions_data.get('flat_list_zh', []))
        self.ai_transitions_en = set(self.transitions_data.get('flat_list_en', []))

        # AI words from ai_words_zh.json
        self.ai_words_zh = set(self.ai_words_data.get('flat_list', []))

        # Passive voice markers
        self.passive_markers_zh = self.transitions_data.get('passive_markers_zh', ['被'])
        self.passive_patterns_zh = self.transitions_data.get('passive_patterns_zh', [])

        # Template patterns (regex)
        templates_zh = self.transitions_data.get('transitions_zh', {}).get('template_patterns', {})
        self.template_patterns_zh = templates_zh.get('patterns', [])

        # Default sentence length threshold (will be updated based on corpus)
        self.avg_sentence_length_zh = 20  # Characters
        self.avg_sentence_length_en = 25  # Words
        self.length_multiplier = 1.5  # Sentences longer than avg * multiplier are flagged

    def _load_transitions(self) -> dict:
        """Load AI transitions from JSON file"""
        try:
            transitions_path = get_resource_path('word_lists/ai_transitions.json')
            if os.path.exists(transitions_path):
                with open(transitions_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def set_corpus_stats(self, avg_sentence_length: float, language: str):
        """Set average sentence length from corpus analysis"""
        if language == 'zh':
            self.avg_sentence_length_zh = avg_sentence_length
        else:
            self.avg_sentence_length_en = avg_sentence_length

    def analyze_text(self, text: str, language: str = None) -> List[SentenceDiagnosis]:
        """Analyze all sentences in text and return diagnoses for problematic sentences"""
        if language is None:
            language = self.language

        sentences = self._split_sentences(text, language)
        results = []

        for idx, (sent_text, start_pos, end_pos) in enumerate(sentences):
            issues = self._check_sentence(sent_text, language)
            # Only include sentences with issues
            if issues:
                results.append(SentenceDiagnosis(
                    index=idx,
                    text=sent_text,
                    start_pos=start_pos,
                    end_pos=end_pos,
                    issues=issues
                ))

        return results

    def _split_sentences(self, text: str, language: str) -> List[Tuple[str, int, int]]:
        """Split text into sentences with their positions
        Returns: List of (sentence_text, start_pos, end_pos)
        """
        return split_sentences_with_positions(text, language)

    def _is_heading_like(self, sentence: str, language: str) -> bool:
        return is_heading_like(sentence, language)

    def _check_sentence(self, sentence: str, language: str) -> List[SentenceIssue]:
        """Check a single sentence for all types of issues"""
        if self._is_heading_like(sentence, language):
            return []

        issues = []

        # Check sentence length
        length_issue = self._check_length(sentence, language)
        if length_issue:
            issues.append(length_issue)

        # Check AI transition words
        transition_issues = self._check_ai_transitions(sentence, language)
        issues.extend(transition_issues)

        # Check AI high-frequency words (from ai_words_zh.json)
        if language in ('zh', 'mixed'):
            ai_word_issues = self._check_ai_words(sentence)
            issues.extend(ai_word_issues)

        # Check passive voice
        if language in ('zh', 'mixed'):
            passive_issue = self._check_passive(sentence)
            if passive_issue:
                issues.append(passive_issue)

        # Check template patterns
        if language in ('zh', 'mixed'):
            template_issues = self._check_templates(sentence)
            issues.extend(template_issues)

        return issues

    def _check_length(self, sentence: str, language: str) -> Optional[SentenceIssue]:
        """Check if sentence is too long"""
        if language == 'zh' or language == 'mixed':
            # Count Chinese characters
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', sentence))
            threshold = int(self.avg_sentence_length_zh * self.length_multiplier)
            if chinese_chars > threshold:
                return SentenceIssue(
                    issue_type="long_sentence",
                    description=t("style.long_sentence", count=chinese_chars, suggested=threshold),
                    severity="info"
                )
        else:
            # Count words for English
            words = len(re.findall(r'\b[a-z]+\b', sentence.lower()))
            threshold = int(self.avg_sentence_length_en * self.length_multiplier)
            if words > threshold:
                return SentenceIssue(
                    issue_type="long_sentence",
                    description=t("style.long_sentence_en", count=words, suggested=threshold),
                    severity="info"
                )
        return None

    def _check_ai_transitions(self, sentence: str, language: str) -> List[SentenceIssue]:
        """Check for AI-style transition words/phrases"""
        issues = []

        if language in ('zh', 'mixed'):
            for transition in self.ai_transitions_zh:
                if transition in sentence:
                    issues.append(SentenceIssue(
                        issue_type="ai_transition",
                        description=t("style.ai_transition", word=transition),
                        severity="warning",
                        matched_text=transition
                    ))

        if language in ('en', 'mixed'):
            sentence_lower = sentence.lower()
            for transition in self.ai_transitions_en:
                if transition.lower() in sentence_lower:
                    issues.append(SentenceIssue(
                        issue_type="ai_transition",
                        description=t("style.ai_transition", word=transition),
                        severity="warning",
                        matched_text=transition
                    ))

        return issues

    def _check_ai_words(self, sentence: str) -> List[SentenceIssue]:
        """Check for AI high-frequency words from ai_words_zh.json"""
        issues = []
        for word in self.ai_words_zh:
            if word in sentence:
                issues.append(SentenceIssue(
                    issue_type="ai_word",
                    description=t("style.ai_word", word=word),
                    severity="info",
                    matched_text=word
                ))
        return issues

    def _check_passive(self, sentence: str) -> Optional[SentenceIssue]:
        """Check for excessive passive voice usage (Chinese)"""
        # Simple check for passive markers
        for marker in self.passive_markers_zh:
            if marker in sentence:
                return SentenceIssue(
                    issue_type="passive",
                    description=t("style.passive_voice"),
                    severity="info",
                    matched_text=marker
                )

        # Check passive patterns
        for pattern in self.passive_patterns_zh:
            if re.search(pattern, sentence):
                return SentenceIssue(
                    issue_type="passive",
                    description=t("style.passive_voice"),
                    severity="info"
                )

        return None

    def _check_templates(self, sentence: str) -> List[SentenceIssue]:
        """Check for template/formulaic patterns"""
        issues = []
        for pattern in self.template_patterns_zh:
            match = re.search(pattern, sentence)
            if match:
                issues.append(SentenceIssue(
                    issue_type="template",
                    description=t("style.template_pattern", pattern=match.group()),
                    severity="info",
                    matched_text=match.group()
                ))
        return issues

    def get_summary(self, diagnoses: List[SentenceDiagnosis]) -> dict:
        """Get summary statistics of all issues found"""
        summary = {
            'total_sentences_with_issues': len(diagnoses),
            'issue_counts': Counter(),
            'issues_by_type': {}
        }

        for diag in diagnoses:
            for issue in diag.issues:
                summary['issue_counts'][issue.issue_type] += 1
                if issue.issue_type not in summary['issues_by_type']:
                    summary['issues_by_type'][issue.issue_type] = []
                summary['issues_by_type'][issue.issue_type].append({
                    'sentence_index': diag.index,
                    'description': issue.description,
                    'matched_text': issue.matched_text
                })

        return summary


class ModernButton(tk.Canvas):
    """Smooth pill-shaped button with hover animation"""
    def __init__(self, parent, text, command=None, width=100, height=36,
                 bg=Theme.BG_TERTIARY, hover_bg=Theme.BG_HOVER, fg=Theme.TEXT_PRIMARY,
                 font_size=11, accent=False, **kwargs):
        super().__init__(parent, width=width, height=height,
                        bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.accent = bool(accent)
        self.bg = Theme.PRIMARY if self.accent else bg
        self.hover_bg = Theme.PRIMARY_HOVER if self.accent else hover_bg
        self.fg = "white" if self.accent else fg
        self.disabled_bg = Theme.BG_TERTIARY
        self.disabled_fg = Theme.TEXT_MUTED
        self.text = text
        self.font_size = font_size
        self._width = width
        self._height = height
        self.enabled = True
        self.draw_button(self.bg, self.fg)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)

    def draw_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Draw a smooth rounded rectangle"""
        points = [
            x1+radius, y1,
            x2-radius, y1,
            x2, y1,
            x2, y1+radius,
            x2, y2-radius,
            x2, y2,
            x2-radius, y2,
            x1+radius, y2,
            x1, y2,
            x1, y2-radius,
            x1, y1+radius,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def draw_button(self, color, text_color=None):
        self.delete("all")
        r = min(self._height // 2, 18)  # Pill shape
        self.draw_rounded_rect(0, 0, self._width, self._height, r, fill=color, outline="")
        self.create_text(self._width//2, self._height//2, text=self.text,
                        fill=(text_color or self.fg), font=(FONT_UI, self.font_size))

    def set_text(self, text):
        self.text = text
        if self.enabled:
            self.draw_button(self.bg, self.fg)
        else:
            self.draw_button(self.disabled_bg, self.disabled_fg)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled:
            self.draw_button(self.bg, self.fg)
        else:
            self.draw_button(self.disabled_bg, self.disabled_fg)

    def on_enter(self, e):
        if not self.enabled:
            self.config(cursor="arrow")
            return
        self.draw_button(self.hover_bg, self.fg if not self.accent else "white")
        self.config(cursor="hand2")

    def on_leave(self, e):
        if not self.enabled:
            self.draw_button(self.disabled_bg, self.disabled_fg)
            return
        self.draw_button(self.bg, self.fg)

    def on_click(self, e):
        if self.enabled and self.command:
            self.command()


class IconButton(tk.Canvas):
    """Small circular icon button"""
    def __init__(self, parent, text, command=None, width=28, height=28,
                 bg=Theme.BG_TERTIARY, hover_bg=Theme.PRIMARY, fg=Theme.TEXT_SECONDARY,
                 **kwargs):
        super().__init__(parent, width=width, height=height,
                        bg=parent["bg"], highlightthickness=0, **kwargs)
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg
        self.fg = fg
        self.disabled_bg = Theme.BG_TERTIARY
        self.disabled_fg = Theme.TEXT_MUTED
        self.text = text
        self._width = width
        self._height = height
        self.enabled = True
        self.draw_button(self.bg, self.fg)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", self.on_click)

    def draw_button(self, color, text_color):
        self.delete("all")
        # Draw circle
        self.create_oval(0, 0, self._width, self._height, fill=color, outline="")
        self.create_text(self._width//2, self._height//2, text=self.text,
                        fill=text_color, font=(FONT_UI, 11, "bold"))

    def on_enter(self, e):
        if not self.enabled:
            self.config(cursor="arrow")
            return
        self.draw_button(self.hover_bg, "white")
        self.config(cursor="hand2")

    def on_leave(self, e):
        if not self.enabled:
            self.draw_button(self.disabled_bg, self.disabled_fg)
            return
        self.draw_button(self.bg, self.fg)

    def on_click(self, e):
        if self.enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled:
            self.draw_button(self.bg, self.fg)
        else:
            self.draw_button(self.disabled_bg, self.disabled_fg)


class ModernProgressBar(tk.Canvas):
    """Modern rounded progress bar with smooth animation."""

    def __init__(
        self,
        parent,
        height: int = 10,
        track_color: str = None,
        fill_color: str = None,
        radius: int = None,
        **kwargs,
    ):
        super().__init__(parent, height=height, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.track_color = track_color or Theme.BG_TERTIARY
        self.fill_color = fill_color or Theme.PRIMARY
        self._height = int(height)
        self._radius = int(radius) if radius is not None else max(3, int(height // 2))
        self._display_fraction = 0.0
        self._target_fraction = 0.0
        self._anim_after_id = None
        self._anim_start = 0.0
        self._anim_from = 0.0
        self._anim_to = 0.0
        self._anim_duration_ms = 180

        self._indeterminate = False
        self._indeterminate_after_id = None
        self._indeterminate_phase = 0.0
        self._indeterminate_last_t = None

        self.bind("<Configure>", self._on_configure)

    def _rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        r = max(0, min(int(radius), int((y2 - y1) // 2)))
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _on_configure(self, _event=None):
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = int(self.winfo_width() or 0)
        h = int(self.winfo_height() or self._height or 10)
        if w <= 2 or h <= 2:
            return
        r = min(int(self._radius), int(h // 2))

        # Track
        self._rounded_rect(0, 0, w, h, r, fill=self.track_color, outline="")

        # Indeterminate shimmer (useful when progress is stuck at 0%)
        if bool(getattr(self, "_indeterminate", False)):
            seg_w = max(40, int(w * 0.28))
            seg_w = min(seg_w, w)
            phase = float(getattr(self, "_indeterminate_phase", 0.0) or 0.0) % 1.0
            x = int(round((w + seg_w) * phase - seg_w))
            x1 = max(0, x)
            x2 = min(w, x + seg_w)
            if x2 > x1:
                self._rounded_rect(x1, 0, x2, h, r, fill=self.fill_color, outline="")
            return

        # Fill (determinate)
        frac = float(self._display_fraction or 0.0)
        if frac <= 0.0:
            return
        fill_w = max(1, int(round(w * frac)))
        fill_w = min(w, fill_w)
        self._rounded_rect(0, 0, fill_w, h, r, fill=self.fill_color, outline="")

    def set_indeterminate(self, active: bool):
        active = bool(active)
        if active == bool(getattr(self, "_indeterminate", False)):
            return

        self._indeterminate = active

        # Stop determinate animation when switching modes.
        if self._anim_after_id:
            try:
                self.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None

        if not active:
            if self._indeterminate_after_id:
                try:
                    self.after_cancel(self._indeterminate_after_id)
                except Exception:
                    pass
                self._indeterminate_after_id = None
            self._indeterminate_last_t = None
            self._redraw()
            return

        self._indeterminate_last_t = time.monotonic()
        self._indeterminate_phase = float(getattr(self, "_indeterminate_phase", 0.0) or 0.0) % 1.0
        self._indeterminate_after_id = self.after(16, self._tick_indeterminate)

    def _tick_indeterminate(self):
        self._indeterminate_after_id = None
        if not bool(getattr(self, "_indeterminate", False)):
            return
        now = time.monotonic()
        last = float(getattr(self, "_indeterminate_last_t", now) or now)
        dt = max(0.0, min(0.2, now - last))
        self._indeterminate_last_t = now

        # Advance: about one full sweep per ~1.6s
        self._indeterminate_phase = (float(getattr(self, "_indeterminate_phase", 0.0) or 0.0) + dt * 0.62) % 1.0
        self._redraw()
        self._indeterminate_after_id = self.after(16, self._tick_indeterminate)

    def set_progress(self, fraction: float, animate: bool = True):
        frac = max(0.0, min(1.0, float(fraction or 0.0)))
        self._target_fraction = frac
        if bool(getattr(self, "_indeterminate", False)):
            # Keep rendering indeterminate; fraction is still tracked for when determinate resumes.
            self._display_fraction = frac
            return
        if not animate:
            self._display_fraction = frac
            self._redraw()
            return

        if self._anim_after_id:
            try:
                self.after_cancel(self._anim_after_id)
            except Exception:
                pass
            self._anim_after_id = None

        self._anim_start = time.monotonic()
        self._anim_from = float(self._display_fraction or 0.0)
        self._anim_to = float(frac)
        self._anim_after_id = self.after(16, self._tick_animation)

    def _tick_animation(self):
        self._anim_after_id = None
        now = time.monotonic()
        elapsed_ms = (now - self._anim_start) * 1000.0
        t01 = 1.0 if self._anim_duration_ms <= 0 else max(0.0, min(1.0, elapsed_ms / self._anim_duration_ms))
        # Ease out cubic
        eased = 1.0 - (1.0 - t01) ** 3
        self._display_fraction = self._anim_from + (self._anim_to - self._anim_from) * eased
        self._redraw()
        if t01 < 1.0:
            self._anim_after_id = self.after(16, self._tick_animation)


class RoundedPanel(tk.Canvas):
    """A panel with smooth rounded corners"""
    def __init__(self, parent, bg=Theme.BG_SECONDARY, radius=CORNER_RADIUS, **kwargs):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, **kwargs)
        self.panel_bg = bg
        self.radius = radius
        self._inner_frame = None
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event):
        self.delete("bg")
        w, h = event.width, event.height
        r = self.radius
        # Draw rounded rectangle background
        points = [
            r, 0,
            w-r, 0,
            w, 0,
            w, r,
            w, h-r,
            w, h,
            w-r, h,
            r, h,
            0, h,
            0, h-r,
            0, r,
            0, 0,
        ]
        self.create_polygon(points, smooth=True, fill=self.panel_bg, outline="", tags="bg")
        self.tag_lower("bg")

    def get_inner_frame(self):
        if self._inner_frame is None:
            self._inner_frame = tk.Frame(self, bg=self.panel_bg)
            self.create_window(0, 0, window=self._inner_frame, anchor="nw", tags="inner")
            self.bind("<Configure>", lambda e: (self._on_configure(e),
                      self.itemconfig("inner", width=e.width, height=e.height)))
        return self._inner_frame


class ToolTip:
    """Tooltip widget for displaying hover information"""

    def __init__(self, widget, text="", delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self.scheduled_id = None

        self.widget.bind("<Enter>", self._schedule_show)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<Motion>", self._update_position)

    def _schedule_show(self, event=None):
        self._hide()
        if self.text:
            self.scheduled_id = self.widget.after(self.delay, self._show)

    def _show(self, event=None):
        if self.tooltip_window or not self.text:
            return

        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        # Create tooltip frame with styled appearance
        frame = tk.Frame(tw, bg=Theme.BG_TERTIARY, relief=tk.SOLID, borderwidth=1)
        frame.pack()

        label = tk.Label(frame, text=self.text, justify=tk.LEFT,
                        bg=Theme.BG_TERTIARY, fg=Theme.TEXT_PRIMARY,
                        font=(FONT_UI, 10), padx=8, pady=6, wraplength=350)
        label.pack()

    def _hide(self, event=None):
        if self.scheduled_id:
            self.widget.after_cancel(self.scheduled_id)
            self.scheduled_id = None
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

    def _update_position(self, event=None):
        if self.tooltip_window and event is not None:
            x = self.widget.winfo_rootx() + event.x + 15
            y = self.widget.winfo_rooty() + event.y + 15
            self.tooltip_window.wm_geometry(f"+{x}+{y}")

    def update_text(self, text: str):
        self.text = text or ""


def copy_to_clipboard(root: tk.Tk, text: str):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update_idletasks()


class TextToolTip:
    """Tooltip for Text widget that shows info based on text position"""

    def __init__(self, text_widget, get_tooltip_func):
        """
        text_widget: The Text widget to attach to
        get_tooltip_func: Function that takes (text_index) and returns tooltip text or None
        """
        self.text_widget = text_widget
        self.get_tooltip_func = get_tooltip_func
        self.tooltip_window = None
        self.scheduled_id = None
        self.last_index = None

        self.text_widget.bind("<Motion>", self._on_motion)
        self.text_widget.bind("<Leave>", self._hide)

    def _on_motion(self, event):
        # Get index at mouse position
        try:
            index = self.text_widget.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return

        # Check if we're over a tagged region with issues
        tags = self.text_widget.tag_names(index)

        if "style_issue" in tags and index != self.last_index:
            self.last_index = index
            self._schedule_show(event, index)
        elif "style_issue" not in tags:
            self._hide()
            self.last_index = None

    def _schedule_show(self, event, index):
        self._hide()
        self.scheduled_id = self.text_widget.after(300, lambda: self._show(event, index))

    def _show(self, event, index):
        if self.tooltip_window:
            return

        tooltip_text = self.get_tooltip_func(index)
        if not tooltip_text:
            return

        x = self.text_widget.winfo_rootx() + event.x + 15
        y = self.text_widget.winfo_rooty() + event.y + 15

        self.tooltip_window = tw = tk.Toplevel(self.text_widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        # Create tooltip frame
        frame = tk.Frame(tw, bg=Theme.BG_SECONDARY, relief=tk.SOLID, borderwidth=1)
        frame.pack()

        # Header
        header = tk.Label(frame, text=t("style.issues_found"),
                         bg=Theme.BG_SECONDARY, fg=Theme.WARNING,
                         font=(FONT_UI, 10, "bold"), padx=8, pady=4, anchor='w')
        header.pack(fill=tk.X)

        # Issues list
        label = tk.Label(frame, text=tooltip_text, justify=tk.LEFT,
                        bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY,
                        font=(FONT_UI, 10), padx=8, pady=6, wraplength=400)
        label.pack()

    def _hide(self, event=None):
        if self.scheduled_id:
            self.text_widget.after_cancel(self.scheduled_id)
            self.scheduled_id = None
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class ModernApp:
    def __init__(self, root):
        self.root = root
        self.settings = Settings()
        self.i18n = get_i18n()

        # Load and apply theme
        self.dark_mode = self.settings.get('dark_mode', False)
        Theme.set_mode(self.dark_mode)

        saved_lang = self.settings.get('language', 'en')
        set_language(saved_lang)

        self.root.title(f"{t('app.title')} v{VERSION}")
        self.root.geometry("1400x900")
        self.root.configure(bg=Theme.BG_PRIMARY)
        self.root.minsize(1100, 750)
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        except Exception:
            pass

        # Library management
        self.library_manager = LibraryManager()
        self.current_library = self.settings.get('current_library', None)

        # Initialize corpus with current library
        library_path = None
        if self.current_library:
            library_path = self.library_manager.get_library_path(self.current_library)
            if not os.path.exists(library_path):
                self.current_library = None
                library_path = None

        self.corpus = AcademicCorpus(library_path)
        self.font_size = self.settings.get('font_size', 13)
        self.detected_language = 'en'
        self.last_word_stats = []
        self.last_phrase_stats = []
        self.last_weirdness = {}
        self.stats_view = 'words'  # 'words' | 'phrases'

        # Style analysis
        self.style_analyzer = None
        self._all_sentence_diagnoses = []
        self.last_sentence_diagnoses = []
        self._show_minor_issues = bool(self.settings.get("show_minor_issues", False))
        self._analysis_ran = False
        self.sentence_issue_map = {}  # Maps text positions to issues
        self._load_style_analyzer()

        # Semantic similarity (corpus sentence retrieval)
        self.semantic_embedder = None
        self.semantic_index = None
        self.semantic_index_library_path = None
        self._load_semantic_embedder()

        # Syntax analysis (UDPipe) - optional but improves sentence-level diagnostics
        self.syntax_analyzer = None
        self.syntax_model_dir = None
        self.syntax_analyzer_error = ""
        self._load_syntax_analyzer()

        self._widgets = {}
        self.create_ui()
        self.load_vocabulary()
        self.i18n.register_callback(self.refresh_ui)

        # Default: semantic model is required. If missing, show an explicit error (exe mode).
        if getattr(sys, "frozen", False) and self.semantic_embedder is None:
            self.root.after(300, lambda: self._require_semantic_model())

        self._status_flash_after_id = None

        # Long-running task state (PDF processing / semantic indexing)
        self._busy = False
        self._cancel_event = None
        self._busy_thread = None
        self._task_name = ""
        self._task_start_time = 0.0
        self._stage_name = ""
        self._stage_start_time = 0.0
        self._stage_last_update_time = 0.0
        self._stage_last_done = 0
        self._stage_last_total = 0
        self._stage_last_detail = ""
        self._stage_speed_ema = None

    def _load_style_analyzer(self):
        """Initialize the style analyzer with AI words data"""
        try:
            ai_words_path = get_resource_path('word_lists/ai_words_zh.json')
            ai_words_data = {}
            if os.path.exists(ai_words_path):
                with open(ai_words_path, 'r', encoding='utf-8') as f:
                    ai_words_data = json.load(f)
            self.style_analyzer = StyleAnalyzer(ai_words_data)
        except Exception:
            self.style_analyzer = StyleAnalyzer()

    def _handle_diag_mousewheel(self, event):
        """Sentence diagnosis: Ctrl+wheel zoom (scrolling handled by widget)."""
        try:
            ctrl = (event.state & 0x4) != 0
        except Exception:
            ctrl = False
        if ctrl:
            try:
                if getattr(event, "delta", 0) > 0:
                    self.increase_font()
                else:
                    self.decrease_font()
            except Exception:
                pass
            return "break"
        return None

    def _load_semantic_embedder(self):
        """Initialize semantic embedder from a local model folder (offline)."""
        candidates = []
        errors = []
        self.semantic_model_dir = None
        self.semantic_embedder_error = ""

        # User-configured path (optional)
        user_path = self.settings.get('semantic_model_dir', None)
        if user_path:
            candidates.append(user_path)

        # Next to the executable (installer package)
        candidates.append(os.path.join(get_app_dir(), "models", "semantic"))

        # Dev folder (repo)
        candidates.append(get_resource_path(os.path.join("models", "semantic")))

        def has_model_files(path: str) -> bool:
            if not path or not os.path.exists(path):
                return False
            onnx_ok = os.path.exists(os.path.join(path, "model.onnx")) or os.path.exists(os.path.join(path, "onnx", "model.onnx"))
            tok_ok = os.path.exists(os.path.join(path, "tokenizer.json"))
            return onnx_ok and tok_ok

        for path in candidates:
            try:
                if not has_model_files(path):
                    continue
                self.semantic_embedder = SemanticEmbedder(path, model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2")
                self.semantic_model_dir = path
                self.semantic_embedder_error = ""
                return
            except Exception as e:
                errors.append(f"{path}: {e}")
                continue

        self.semantic_embedder = None
        self.semantic_model_dir = None
        self.semantic_embedder_error = "\n".join(errors[-3:]) if errors else ""

    def _load_syntax_analyzer(self):
        """Initialize a local UDPipe syntax analyzer when models are available."""
        candidates = []
        errors = []
        self.syntax_model_dir = None
        self.syntax_analyzer_error = ""

        if UDPipeModel is None or UDPipePipeline is None or UDPipeProcessingError is None:
            self.syntax_analyzer = None
            self.syntax_analyzer_error = "Missing dependency: ufal.udpipe"
            return

        user_path = self.settings.get('syntax_model_dir', None)
        if user_path:
            candidates.append(user_path)

        candidates.append(os.path.join(get_app_dir(), "models", "syntax"))
        candidates.append(get_resource_path(os.path.join("models", "syntax")))

        def has_models(path: str) -> bool:
            if not path or not os.path.exists(path):
                return False
            return (
                os.path.exists(os.path.join(path, "en.udpipe"))
                or os.path.exists(os.path.join(path, "zh.udpipe"))
            )

        for path in candidates:
            try:
                if not has_models(path):
                    continue
                self.syntax_analyzer = UDPipeSyntaxAnalyzer(path)
                self.syntax_model_dir = path
                self.syntax_analyzer_error = ""
                return
            except Exception as e:
                errors.append(f"{path}: {e}")

        self.syntax_analyzer = None
        self.syntax_model_dir = None
        self.syntax_analyzer_error = "\n".join(errors[-3:]) if errors else ""

    def _semantic_expected_dir(self) -> str:
        return os.path.join(get_app_dir(), "models", "semantic")

    def _require_semantic_model(self) -> bool:
        if self.semantic_embedder is not None:
            return True
        details = self.semantic_embedder_error.strip() or "-"
        expected = self._semantic_expected_dir()
        expected_has_files = (
            os.path.exists(os.path.join(expected, "tokenizer.json"))
            and (
                os.path.exists(os.path.join(expected, "model.onnx"))
                or os.path.exists(os.path.join(expected, "onnx", "model.onnx"))
            )
        )
        if expected_has_files:
            messagebox.showerror(
                t("semantic.unavailable_title"),
                t("semantic.unavailable_message", expected=expected, details=details),
            )
        else:
            messagebox.showerror(
                t("semantic.missing_title"),
                t("semantic.missing_message", expected=expected, details=details),
            )
        return False

    def _require_semantic_index(self) -> bool:
        if self.corpus is None or not getattr(self.corpus, "library_path", None):
            return True
        if self.corpus.semantic_index_exists():
            # Detect model/index mismatch: users often swap models but keep an old index, making results look unchanged.
            try:
                meta = getattr(self.corpus, "semantic_meta", {}) if self.corpus is not None else {}
            except Exception:
                meta = {}
            if isinstance(meta, dict) and self.semantic_embedder is not None:
                idx_id = (meta.get("model_id", "") or "").strip()
                cur_id = (getattr(self.semantic_embedder, "model_id", "") or "").strip()
                idx_fp = meta.get("model_fingerprint", {}) if isinstance(meta.get("model_fingerprint", {}), dict) else {}
                cur_fp = getattr(self.semantic_embedder, "model_fingerprint", lambda: {})()
                mismatch = False
                # Old libraries may store a generic id like "semantic" (folder name); don't block on that.
                generic_ids = {"semantic", "model", "models", "onnx", "unknown"}
                if (
                    idx_id
                    and cur_id
                    and idx_id != cur_id
                    and idx_id.lower() not in generic_ids
                    and cur_id.lower() not in generic_ids
                ):
                    mismatch = True
                if idx_fp and cur_fp and idx_fp != cur_fp:
                    mismatch = True

                if mismatch:
                    try:
                        ok = messagebox.askyesno(
                            t("semantic.index_mismatch_title"),
                            t(
                                "semantic.index_mismatch_message",
                                current=cur_id or "-",
                                index=idx_id or "-",
                                updated_at=(meta.get("updated_at", "") or "").strip(),
                            ),
                        )
                    except Exception:
                        ok = False
                    if ok:
                        try:
                            self._rebuild_semantic_index_async()
                        except Exception:
                            pass
                    return False
            return True
        messagebox.showerror(
            t("semantic.no_index_title"),
            t("semantic.no_index_message", path=self.corpus.library_path),
        )
        return False

    def _rebuild_semantic_index_async(self):
        """Rebuild embeddings.npy from existing *.sentences.json using the current semantic model."""
        if self.corpus is None or not getattr(self.corpus, "library_path", None):
            return
        if not self._require_semantic_model():
            return
        paths = self.corpus.get_semantic_index_paths()
        sent_path = paths.get("sentences", "")
        emb_path = paths.get("embeddings", "")
        if not sent_path or not os.path.exists(sent_path):
            messagebox.showerror(t("semantic.no_index_title"), t("semantic.no_index_message", path=self.corpus.library_path))
            return
        if not emb_path:
            messagebox.showerror(t("semantic.no_index_title"), t("semantic.no_index_message", path=self.corpus.library_path))
            return

        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self._start_task_ui("rebuild_semantic")
        self._update_task_progress_ui("embed", 0, 0, "")

        def process():
            def ui(fn):
                try:
                    self.root.after(0, fn)
                except Exception:
                    pass

            def ui_set_status(message: str, fg: str):
                ui(lambda m=message, c=fg: self.status_label.config(text=m, fg=c))

            try:
                with open(sent_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, list) or not raw:
                    raise RuntimeError("sentences.json is empty or invalid")

                sentences: List[str] = []
                for item in raw:
                    if isinstance(item, str):
                        sentences.append(item)
                        continue
                    if isinstance(item, dict):
                        text = item.get("text", None)
                        if not isinstance(text, str):
                            text = item.get("sentence", "")
                        sentences.append(text if isinstance(text, str) else "")
                        continue
                    sentences.append("")

                total = int(len(sentences))
                ui(lambda: self._update_task_progress_ui("embed", 0, total, ""))

                def report(done, total_):
                    ui_set_status(t("status.embedding", current=done, total=total_), Theme.WARNING)
                    ui(lambda d=done, tt=total_: self._update_task_progress_ui("embed", d, tt, ""))

                embeddings = self.semantic_embedder.embed(
                    sentences,
                    batch_size=SEMANTIC_EMBED_BATCH,
                    progress_callback=report,
                    progress_every_s=SEMANTIC_PROGRESS_EVERY_S,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    raise CancelledError()

                if np is None:
                    raise RuntimeError("Missing dependency: numpy")
                tmp_embeddings = emb_path[:-4] + ".tmp.npy" if emb_path.endswith(".npy") else emb_path + ".tmp.npy"
                np.save(tmp_embeddings, embeddings.astype(np.float32, copy=False))
                os.replace(tmp_embeddings, emb_path)

                # Update library meta to reflect the new model.
                self.corpus.semantic_meta = {
                    "model_id": getattr(self.semantic_embedder, "model_id", "") or "",
                    "dim": int(embeddings.shape[1]) if getattr(embeddings, "ndim", 0) == 2 else 0,
                    "sentence_count": int(len(sentences)),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "model_fingerprint": getattr(self.semantic_embedder, "model_fingerprint", lambda: {})(),
                }
                try:
                    self.corpus.save_vocabulary()
                except Exception:
                    pass
                self.semantic_index = None
                self.semantic_index_library_path = None

                ui_set_status(t("semantic.rebuild_done"), Theme.SUCCESS)
                ui(lambda: self._finish_task_ui())
                ui(lambda: messagebox.showinfo(t("msg.complete"), t("semantic.rebuild_success")))
            except CancelledError:
                try:
                    # Best-effort cleanup temp file
                    tmp_embeddings = emb_path[:-4] + ".tmp.npy" if emb_path.endswith(".npy") else emb_path + ".tmp.npy"
                    if tmp_embeddings and os.path.exists(tmp_embeddings):
                        os.remove(tmp_embeddings)
                except Exception:
                    pass
                ui_set_status(t("progress.canceled"), Theme.WARNING)
                ui(lambda: self._finish_task_ui())
            except Exception as e:
                ui_set_status(t("progress.failed"), Theme.DANGER)
                ui(lambda err=str(e): messagebox.showerror(t("msg.error"), err))
                ui(lambda: self._finish_task_ui())

        self._busy_thread = threading.Thread(target=process, daemon=True)
        self._busy_thread.start()

    def create_ui(self):
        # ========== Header Bar ==========
        header = tk.Frame(self.root, bg=Theme.BG_SECONDARY, height=56)
        header.pack(fill=tk.X, padx=16, pady=(16, 0))
        header.pack_propagate(False)

        # Round the header corners using a canvas overlay approach
        header_inner = tk.Frame(header, bg=Theme.BG_SECONDARY)
        header_inner.pack(fill=tk.BOTH, expand=True, padx=20, pady=12)

        # Title
        title_frame = tk.Frame(header_inner, bg=Theme.BG_SECONDARY)
        title_frame.pack(side=tk.LEFT)

        self._widgets['title_label'] = tk.Label(title_frame,
                text=t('app.title'),
                font=(FONT_UI, 15, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['title_label'].pack(side=tk.LEFT)

        tk.Label(title_frame, text=f"  v{VERSION}",
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, pady=(3, 0))

        # Right side: Status + Language + Font controls
        right_controls = tk.Frame(header_inner, bg=Theme.BG_SECONDARY)
        right_controls.pack(side=tk.RIGHT)

        # Status
        self.status_label = tk.Label(right_controls, text=t("status.loading"),
                                    font=(FONT_UI, 10),
                                    bg=Theme.BG_SECONDARY, fg=Theme.SUCCESS)
        self.status_label.pack(side=tk.LEFT, padx=(0, 20))

        # Language selector (styled)
        lang_frame = tk.Frame(right_controls, bg=Theme.BG_TERTIARY)
        lang_frame.pack(side=tk.LEFT, padx=(0, 12))

        self.lang_var = tk.StringVar(value=get_language())
        self._widgets['lang_dropdown'] = ttk.Combobox(lang_frame, textvariable=self.lang_var,
                                    values=['en', 'zh_CN'], width=6, state='readonly',
                                    font=(FONT_UI, 10))
        self._widgets['lang_dropdown'].pack(padx=4, pady=4)
        self._widgets['lang_dropdown'].bind('<<ComboboxSelected>>', self.on_language_change)

        # Font size controls
        font_frame = tk.Frame(right_controls, bg=Theme.BG_SECONDARY)
        font_frame.pack(side=tk.LEFT)

        tk.Label(font_frame, text="A", font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))

        self._widgets['btn_font_minus'] = IconButton(font_frame, "−", self.decrease_font)
        self._widgets['btn_font_minus'].pack(side=tk.LEFT, padx=2)

        self.font_label = tk.Label(font_frame, text=str(self.font_size),
                                  font=(FONT_UI, 10, "bold"),
                                  bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY, width=2)
        self.font_label.pack(side=tk.LEFT)

        self._widgets['btn_font_plus'] = IconButton(font_frame, "+", self.increase_font)
        self._widgets['btn_font_plus'].pack(side=tk.LEFT, padx=2)

        # Theme toggle (sun/moon icon)
        theme_icon = "☀" if self.dark_mode else "☾"
        self._widgets['btn_theme'] = IconButton(right_controls, theme_icon,
                                                self.toggle_theme, width=28, height=28)
        self._widgets['btn_theme'].pack(side=tk.LEFT, padx=(12, 0))

        # ========== Toolbar ==========
        toolbar = tk.Frame(self.root, bg=Theme.BG_PRIMARY, height=50)
        toolbar.pack(fill=tk.X, padx=16, pady=(12, 8))

        # Library selector section
        lib_frame = tk.Frame(toolbar, bg=Theme.BG_PRIMARY)
        lib_frame.pack(side=tk.LEFT)

        tk.Label(lib_frame, text=t("library.label"), font=(FONT_UI, 10),
                bg=Theme.BG_PRIMARY, fg=Theme.TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))

        # Library dropdown
        self.library_var = tk.StringVar(value=self.current_library or "")
        self._widgets['lib_dropdown'] = ttk.Combobox(lib_frame, textvariable=self.library_var,
                                    width=15, state='readonly', font=(FONT_UI, 10))
        self._widgets['lib_dropdown'].pack(side=tk.LEFT, padx=(0, 8))
        self._widgets['lib_dropdown'].bind('<<ComboboxSelected>>', self.on_library_change)
        self.update_library_dropdown()

        # Library buttons
        self._widgets['btn_new_lib'] = ModernButton(lib_frame, "+",
                    self.create_new_library, width=36, height=32, font_size=12)
        self._widgets['btn_new_lib'].pack(side=tk.LEFT, padx=(0, 4))

        self._widgets['btn_lib_menu'] = ModernButton(lib_frame, "⋮",
                    self.show_library_menu, width=36, height=32, font_size=14)
        self._widgets['btn_lib_menu'].pack(side=tk.LEFT, padx=(0, 16))

        # Action buttons
        self._widgets['btn_load_pdf'] = ModernButton(toolbar, t("toolbar.load_pdf"),
                    self.select_pdf_folder, width=100, height=36, font_size=11)
        self._widgets['btn_load_pdf'].pack(side=tk.LEFT, padx=(0, 10))

        self._widgets['btn_show_vocab'] = ModernButton(toolbar, t("toolbar.show_vocab"),
                    self.show_vocabulary, width=90, height=36, font_size=11)
        self._widgets['btn_show_vocab'].pack(side=tk.LEFT, padx=(0, 10))

        # ========== Progress Panel (hidden by default) ==========
        self._progress_panel = tk.Frame(self.root, bg=Theme.BG_SECONDARY)
        self._progress_panel_pack_opts = {"fill": tk.X, "padx": 16, "pady": (0, 8)}
        self._progress_panel.pack(**self._progress_panel_pack_opts)

        progress_inner = tk.Frame(self._progress_panel, bg=Theme.BG_SECONDARY)
        progress_inner.pack(fill=tk.X, padx=16, pady=12)

        progress_top = tk.Frame(progress_inner, bg=Theme.BG_SECONDARY)
        progress_top.pack(fill=tk.X)

        self._widgets['progress_title'] = tk.Label(
            progress_top,
            text=t("progress.idle_title"),
            font=(FONT_UI, 11, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        )
        self._widgets['progress_title'].pack(side=tk.LEFT)

        self._widgets['progress_stage'] = tk.Label(
            progress_top,
            text=t("progress.idle_stage"),
            font=(FONT_UI, 10),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        )
        self._widgets['progress_stage'].pack(side=tk.LEFT, padx=(10, 0))

        self._widgets['btn_cancel_task'] = ModernButton(
            progress_top,
            t("progress.btn.cancel"),
            command=self._cancel_current_task,
            width=84,
            height=32,
            font_size=10,
        )
        self._widgets['btn_cancel_task'].pack(side=tk.RIGHT)

        self._widgets['progress_meta'] = tk.Label(
            progress_top,
            text="",
            font=(FONT_UI, 10),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
        )
        self._widgets['progress_meta'].pack(side=tk.RIGHT, padx=(0, 10))

        self._widgets['progress_bar'] = ModernProgressBar(
            progress_inner,
            height=10,
            track_color=Theme.BG_TERTIARY,
            fill_color=Theme.PRIMARY,
        )
        self._widgets['progress_bar'].pack(fill=tk.X, pady=(10, 0))

        progress_bottom = tk.Frame(progress_inner, bg=Theme.BG_SECONDARY)
        progress_bottom.pack(fill=tk.X, pady=(6, 0))

        self._widgets['progress_detail'] = tk.Label(
            progress_bottom,
            text="",
            font=(FONT_UI, 10),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_SECONDARY,
        )
        self._widgets['progress_detail'].pack(side=tk.LEFT)

        self._widgets['progress_percent'] = tk.Label(
            progress_bottom,
            text="",
            font=(FONT_UI, 10, "bold"),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_PRIMARY,
        )
        self._widgets['progress_percent'].pack(side=tk.RIGHT)

        self._widgets['progress_note'] = tk.Label(
            progress_inner,
            text=t("progress.idle_note"),
            font=(FONT_UI, 9),
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_MUTED,
            wraplength=1200,
            justify=tk.LEFT,
        )
        self._widgets['progress_note'].pack(fill=tk.X, pady=(6, 0))

        # Idle state: keep cancel hidden/disabled to reduce noise.
        try:
            if self._widgets.get('btn_cancel_task') and hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                self._widgets['btn_cancel_task'].set_enabled(False)
        except Exception:
            pass

        # ========== Main Split (Content + Bottom) ==========
        main_split = tk.PanedWindow(
            self.root,
            orient=tk.VERTICAL,
            bg=Theme.BG_PRIMARY,
            sashwidth=8,
            relief=tk.FLAT,
            borderwidth=0,
        )
        self._main_split = main_split
        main_split.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # Top: Main content area (Input + Results)
        content = tk.Frame(main_split, bg=Theme.BG_PRIMARY)
        main_split.add(content, minsize=360)

        # Bottom: Stats + Diagnosis (resizable)
        bottom_container = tk.Frame(main_split, bg=Theme.BG_PRIMARY)
        main_split.add(bottom_container, minsize=260)

        def _init_main_sash():
            try:
                h = main_split.winfo_height()
                if h and h > 0:
                    # Default: give more space to sentence diagnosis (bottom).
                    main_split.sash_place(0, 0, int(h * 0.38))
            except Exception:
                pass

        self.root.after(120, _init_main_sash)

        # Content split: Input | Results | Stats (resizable)
        content_split = tk.PanedWindow(
            content,
            orient=tk.HORIZONTAL,
            bg=Theme.BG_PRIMARY,
            sashwidth=8,
            relief=tk.FLAT,
            borderwidth=0,
        )
        content_split.pack(fill=tk.BOTH, expand=True)

        # Left panel: Input
        left_panel = tk.Frame(content_split, bg=Theme.BG_SECONDARY)
        content_split.add(left_panel, minsize=360)

        left_header = tk.Frame(left_panel, bg=Theme.BG_SECONDARY)
        left_header.pack(fill=tk.X, padx=16, pady=(16, 12))

        self._widgets['input_title'] = tk.Label(left_header, text=t("panel.input_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['input_title'].pack(side=tk.LEFT)

        self._widgets['btn_detect'] = ModernButton(left_header, t("panel.analyze"),
                    self.analyze_text, width=90, height=36, font_size=11, accent=True)
        self._widgets['btn_detect'].pack(side=tk.RIGHT)

        self._widgets['lang_indicator'] = tk.Label(left_header, text="",
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.WARNING)
        self._widgets['lang_indicator'].pack(side=tk.RIGHT, padx=12)

        # Input text area with dark styling
        input_frame = tk.Frame(left_panel, bg=Theme.BG_INPUT)
        input_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self.input_text = tk.Text(input_frame, wrap=tk.CHAR,
                                  font=(FONT_MONO, self.font_size),
                                  bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY,
                                  insertbackground=Theme.TEXT_PRIMARY,
                                  relief=tk.FLAT, borderwidth=0, padx=12, pady=12,
                                  selectbackground=Theme.PRIMARY,
                                  selectforeground="white")
        self.input_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar_left = tk.Scrollbar(input_frame, bg=Theme.BG_TERTIARY,
                                     troughcolor=Theme.BG_INPUT)
        scrollbar_left.pack(side=tk.RIGHT, fill=tk.Y)
        self.input_text.config(yscrollcommand=scrollbar_left.set)
        scrollbar_left.config(command=self.input_text.yview)

        # Ctrl+scroll wheel zoom
        def _on_ctrl_wheel_input(event):
            if event.state & 0x4:
                if event.delta > 0:
                    self.increase_font()
                else:
                    self.decrease_font()
                return "break"
        self.input_text.bind("<MouseWheel>", _on_ctrl_wheel_input)

        # Middle panel: Results
        right_panel = tk.Frame(content_split, bg=Theme.BG_SECONDARY)
        content_split.add(right_panel, minsize=420)

        right_header = tk.Frame(right_panel, bg=Theme.BG_SECONDARY)
        right_header.pack(fill=tk.X, padx=16, pady=(16, 12))

        self._widgets['result_title'] = tk.Label(right_header, text=t("panel.result_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['result_title'].pack(side=tk.LEFT)

        # Legend with colored dots
        legend_frame = tk.Frame(right_header, bg=Theme.BG_SECONDARY)
        legend_frame.pack(side=tk.RIGHT)

        # Weirdness badge (domain-based)
        badge_frame = tk.Frame(right_header, bg=Theme.BG_SECONDARY)
        badge_frame.pack(side=tk.RIGHT, padx=(0, 12))
        self._widgets['weirdness_badge'] = tk.Label(
            badge_frame,
            text=t("domain.badge_placeholder"),
            font=(FONT_UI, 10, "bold"),
            bg=Theme.BG_TERTIARY,
            fg=Theme.TEXT_MUTED,
            padx=10,
            pady=4
        )
        self._widgets['weirdness_badge'].pack(side=tk.RIGHT)
        self.weirdness_tooltip = ToolTip(self._widgets['weirdness_badge'], text=t("domain.tooltip.placeholder"))

        self._legend_labels = []
        legend_colors = [Theme.SUCCESS, Theme.NORMAL_COLOR, Theme.WARNING, Theme.DANGER]
        legend_keys = ["stats.common_short", "stats.normal_short", "stats.rare_short", "stats.unseen_short"]

        for color, key in zip(legend_colors, legend_keys):
            tk.Label(legend_frame, text="●", font=(FONT_UI, 10),
                    bg=Theme.BG_SECONDARY, fg=color).pack(side=tk.LEFT, padx=(10, 3))
            lbl = tk.Label(legend_frame, text=t(key), font=(FONT_UI, 10),
                    bg=Theme.BG_SECONDARY, fg=Theme.TEXT_SECONDARY)
            lbl.pack(side=tk.LEFT)
            self._legend_labels.append((lbl, key))

        # Result text area
        result_frame = tk.Frame(right_panel, bg=Theme.BG_INPUT)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        self.result_text = tk.Text(result_frame, wrap=tk.CHAR,
                                   font=(FONT_MONO, self.font_size),
                                   bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY,
                                   relief=tk.FLAT, borderwidth=0, padx=12, pady=12,
                                   state=tk.NORMAL,
                                   selectbackground=Theme.PRIMARY,
                                   selectforeground="white")
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.result_text.tag_configure("common", foreground=Theme.SUCCESS)
        self.result_text.tag_configure("normal", foreground=Theme.NORMAL_COLOR)
        self.result_text.tag_configure("rare", foreground=Theme.WARNING,
                                       font=(FONT_MONO, self.font_size, "bold"))
        self.result_text.tag_configure("unseen", foreground=Theme.DANGER,
                                       font=(FONT_MONO, self.font_size, "bold"))
        # Style issue tag - wavy underline effect (using background highlight)
        self.result_text.tag_configure("style_issue",
                                       underline=True,
                                       underlinefg=Theme.WARNING)

        # Setup tooltip for style issues
        self.result_tooltip = TextToolTip(self.result_text, self._get_tooltip_for_position)

        # Make results selectable/copyable while preventing edits
        self._bind_readonly_text(self.result_text)
        self._install_text_context_menu(self.result_text)

        scrollbar_right = tk.Scrollbar(result_frame, bg=Theme.BG_TERTIARY,
                                      troughcolor=Theme.BG_INPUT)
        scrollbar_right.pack(side=tk.RIGHT, fill=tk.Y)
        self.result_text.config(yscrollcommand=scrollbar_right.set)
        scrollbar_right.config(command=self.result_text.yview)

        # Ctrl+scroll wheel zoom
        def _on_ctrl_wheel_result(event):
            if event.state & 0x4:
                if event.delta > 0:
                    self.increase_font()
                else:
                    self.decrease_font()
                return "break"
        self.result_text.bind("<MouseWheel>", _on_ctrl_wheel_result)

        # Right sidebar: Word Statistics Panel
        stats_panel = tk.Frame(content_split, bg=Theme.BG_SECONDARY)
        content_split.add(stats_panel, minsize=360)

        def _init_content_sashes():
            try:
                w = content_split.winfo_width()
                if w and w > 0:
                    # Default: 33% input | 43% results | 24% stats
                    content_split.sash_place(0, int(w * 0.33), 0)
                    content_split.sash_place(1, int(w * 0.76), 0)
            except Exception:
                pass

        self.root.after(140, _init_content_sashes)

        stats_header = tk.Frame(stats_panel, bg=Theme.BG_SECONDARY)
        stats_header.pack(fill=tk.X, padx=16, pady=(12, 8))

        self._widgets['stats_title'] = tk.Label(stats_header, text=t("stats.panel_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['stats_title'].pack(side=tk.LEFT)

        self._widgets['stats_hint'] = tk.Label(stats_header, text=t("stats.panel_hint"),
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED)
        self._widgets['stats_hint'].pack(side=tk.LEFT, padx=16)

        # View selector (Words / Phrases)
        view_frame = tk.Frame(stats_header, bg=Theme.BG_SECONDARY)
        view_frame.pack(side=tk.RIGHT)

        self._stats_view_labels = {
            'words': t("stats.view.words"),
            'phrases': t("stats.view.phrases"),
        }
        self._stats_view_reverse = {v: k for k, v in self._stats_view_labels.items()}
        self.stats_view_var = tk.StringVar(value=self._stats_view_labels.get(self.stats_view, t("stats.view.words")))
        self._widgets['stats_view_dropdown'] = ttk.Combobox(
            view_frame,
            textvariable=self.stats_view_var,
            values=list(self._stats_view_reverse.keys()),
            width=10,
            state='readonly',
            font=(FONT_UI, 10)
        )
        self._widgets['stats_view_dropdown'].pack(side=tk.LEFT, padx=4, pady=2)
        self._widgets['stats_view_dropdown'].bind('<<ComboboxSelected>>', self.on_stats_view_change)

        self._widgets['btn_copy_stats'] = ModernButton(
            view_frame,
            t("btn.copy_table"),
            self.copy_stats_to_clipboard,
            width=90,
            height=32,
            font_size=10
        )
        self._widgets['btn_copy_stats'].pack(side=tk.LEFT, padx=(10, 0), pady=2)
        self._widgets['tt_copy_stats'] = ToolTip(self._widgets['btn_copy_stats'], text=t("tooltip.copy_stats"))

        # Table container
        table_container = tk.Frame(stats_panel, bg=Theme.BG_TERTIARY)
        table_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        # Table header
        header_row = tk.Frame(table_container, bg=Theme.BG_TERTIARY)
        header_row.pack(fill=tk.X)

        col_widths = [20, 14, 12, 14, 14]
        header_keys = ["stats.word", "stats.doc_freq", "stats.doc_pct",
                       "stats.total_freq", "stats.status"]

        self._table_headers = []
        for key, width in zip(header_keys, col_widths):
            lbl = tk.Label(header_row, text=t(key), font=(FONT_UI, 11, "bold"),
                    bg=Theme.BG_TERTIARY, fg=Theme.TEXT_PRIMARY,
                    width=width, anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=8)
            self._table_headers.append((lbl, key))

        # Apply headers based on current view
        self._apply_stats_view_headers()

        # Scrollable table body
        table_body = tk.Frame(table_container, bg=Theme.BG_INPUT)
        table_body.pack(fill=tk.BOTH, expand=True)

        self.stats_canvas = tk.Canvas(table_body, bg=Theme.BG_INPUT, highlightthickness=0)
        stats_scrollbar = tk.Scrollbar(table_body, orient="vertical",
                                       command=self.stats_canvas.yview,
                                       bg=Theme.BG_TERTIARY, troughcolor=Theme.BG_INPUT)
        self.stats_frame = tk.Frame(self.stats_canvas, bg=Theme.BG_INPUT)

        self.stats_frame.bind("<Configure>",
            lambda e: self.stats_canvas.configure(scrollregion=self.stats_canvas.bbox("all")))

        self._stats_window = self.stats_canvas.create_window((0, 0), window=self.stats_frame, anchor="nw")
        def _on_stats_canvas_resize(event):
            try:
                self.stats_canvas.itemconfigure(self._stats_window, width=event.width)
            except Exception:
                pass
        self.stats_canvas.bind("<Configure>", _on_stats_canvas_resize)
        self.stats_canvas.configure(yscrollcommand=stats_scrollbar.set)

        self.stats_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        stats_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling for stats
        def _on_mousewheel(event):
            self.stats_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        self.stats_canvas.bind("<MouseWheel>", _on_mousewheel)
        self.stats_frame.bind("<MouseWheel>", _on_mousewheel)

        # Placeholder
        self._widgets['stats_placeholder'] = tk.Label(self.stats_frame,
                text=t("stats.placeholder"),
                font=(FONT_UI, 11),
                bg=Theme.BG_INPUT, fg=Theme.TEXT_MUTED)
        self._widgets['stats_placeholder'].pack(pady=30)

        # ========== Bottom Panel (Sentence Diagnosis) ==========
        diag_panel = tk.Frame(bottom_container, bg=Theme.BG_SECONDARY)
        diag_panel.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        diag_header = tk.Frame(diag_panel, bg=Theme.BG_SECONDARY)
        diag_header.pack(fill=tk.X, padx=16, pady=(12, 8))

        self._widgets['diag_title'] = tk.Label(diag_header, text=t("style.panel_title"),
                font=(FONT_UI, 13, "bold"),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
        self._widgets['diag_title'].pack(side=tk.LEFT)

        self._widgets['diag_hint'] = tk.Label(diag_header, text=t("style.panel_hint"),
                font=(FONT_UI, 10),
                bg=Theme.BG_SECONDARY, fg=Theme.TEXT_MUTED)
        self._widgets['diag_hint'].pack(side=tk.LEFT, padx=16)

        diag_actions = tk.Frame(diag_header, bg=Theme.BG_SECONDARY)
        diag_actions.pack(side=tk.RIGHT)

        self.show_minor_var = tk.BooleanVar(value=bool(getattr(self, "_show_minor_issues", False)))
        self._widgets['chk_show_minor'] = tk.Checkbutton(
            diag_actions,
            text=t("style.toggle_minor"),
            variable=self.show_minor_var,
            command=self._on_toggle_minor_issues,
            bg=Theme.BG_SECONDARY,
            fg=Theme.TEXT_SECONDARY,
            activebackground=Theme.BG_SECONDARY,
            activeforeground=Theme.TEXT_PRIMARY,
            selectcolor=Theme.BG_SECONDARY,
            font=(FONT_UI, 10),
            padx=6,
            pady=0,
        )
        self._widgets['chk_show_minor'].pack(side=tk.LEFT, padx=(0, 8))

        self._widgets['btn_copy_diag'] = ModernButton(
            diag_actions,
            t("btn.copy_diagnosis"),
            self.copy_diagnosis_to_clipboard,
            width=90,
            height=32,
            font_size=10
        )
        self._widgets['btn_copy_diag'].pack(padx=4, pady=2)
        self._widgets['tt_copy_diag'] = ToolTip(self._widgets['btn_copy_diag'], text=t("tooltip.copy_diagnosis"))

        # Diagnosis list container
        diag_container = tk.Frame(diag_panel, bg=Theme.BG_INPUT)
        diag_container.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))

        diag_font = max(11, int(getattr(self, "font_size", 13) or 13))
        self.diag_text = tk.Text(
            diag_container,
            wrap=tk.CHAR,
            font=(FONT_UI, diag_font),
            bg=Theme.BG_INPUT,
            fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY,
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=12,
            selectbackground=Theme.PRIMARY,
            selectforeground="white",
        )
        self.diag_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        diag_scrollbar = tk.Scrollbar(
            diag_container,
            orient="vertical",
            command=self.diag_text.yview,
            bg=Theme.BG_TERTIARY,
            troughcolor=Theme.BG_INPUT,
        )
        diag_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.diag_text.configure(yscrollcommand=diag_scrollbar.set)

        self._bind_readonly_text(self.diag_text)
        # Ctrl+wheel zoom (scrolling handled by the Text widget)
        self.diag_text.bind("<MouseWheel>", self._handle_diag_mousewheel)
        # Context menu: right-click shows sentence-level actions
        self.diag_text.bind("<Button-3>", self._on_diag_text_right_click)

        # Placeholder
        try:
            self.diag_text.tag_configure("diag_placeholder", font=(FONT_UI, diag_font), foreground=Theme.TEXT_MUTED)
            self.diag_text.insert("1.0", t("style.placeholder"), ("diag_placeholder",))
        except Exception:
            pass

    def on_language_change(self, event=None):
        new_lang = self.lang_var.get()
        set_language(new_lang)
        self.settings.set('language', new_lang)

    def _flash_status(self, message: str, fg: str = None, duration_ms: int = 1400):
        if not getattr(self, "status_label", None):
            return

        if self._status_flash_after_id:
            try:
                self.root.after_cancel(self._status_flash_after_id)
            except Exception:
                pass
            self._status_flash_after_id = None

        original_text = self.status_label.cget("text")
        original_fg = self.status_label.cget("fg")
        flash_text = message
        self.status_label.config(text=flash_text, fg=fg or Theme.SUCCESS)

        def restore():
            self._status_flash_after_id = None
            try:
                # Only restore if not already changed by other actions
                if self.status_label.cget("text") == flash_text:
                    self.status_label.config(text=original_text, fg=original_fg)
            except Exception:
                pass

        self._status_flash_after_id = self.root.after(duration_ms, restore)

    def _copy_text(self, text: str):
        if not text:
            self._flash_status(t("status.nothing_to_copy"), fg=Theme.WARNING)
            return
        try:
            copy_to_clipboard(self.root, text)
            self._flash_status(t("status.copied"), fg=Theme.SUCCESS)
        except Exception:
            self._flash_status(t("status.copy_failed"), fg=Theme.DANGER)

    def _select_all_text(self, widget: tk.Text):
        try:
            widget.tag_add(tk.SEL, "1.0", tk.END)
            widget.mark_set(tk.INSERT, "1.0")
            widget.see(tk.INSERT)
        except Exception:
            pass

    def _bind_readonly_text(self, widget: tk.Text):
        """Allow selection/copy, but block edits for a Text widget."""

        def on_key(event):
            ctrl = (event.state & 0x4) != 0
            if ctrl and event.keysym.lower() in ("c", "a"):
                return None
            if event.keysym in ("Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next", "Escape"):
                return None
            if event.keysym in ("Shift_L", "Shift_R", "Control_L", "Control_R"):
                return None
            return "break"

        widget.bind("<Key>", on_key)
        widget.bind("<Control-a>", lambda e: (self._select_all_text(widget), "break"))
        widget.bind("<<Paste>>", lambda e: "break")
        widget.bind("<<Cut>>", lambda e: "break")
        widget.bind("<Control-v>", lambda e: "break")
        widget.bind("<Control-x>", lambda e: "break")

    def _install_text_context_menu(self, widget: tk.Text):
        def show_menu(event):
            try:
                has_sel = True
                try:
                    _ = widget.selection_get()
                except tk.TclError:
                    has_sel = False

                menu = tk.Menu(self.root, tearoff=0, bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY)
                menu.add_command(
                    label=t("menu.copy"),
                    command=lambda: widget.event_generate("<<Copy>>"),
                    state=tk.NORMAL if has_sel else tk.DISABLED,
                )
                menu.add_command(
                    label=t("menu.select_all"),
                    command=lambda: self._select_all_text(widget),
                )
                menu.add_command(
                    label=t("menu.copy_all"),
                    command=lambda: self._copy_text(widget.get("1.0", tk.END).rstrip()),
                )
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass

        widget.bind("<Button-3>", show_menu)

    def _diag_from_text_event(self, event) -> Optional["SentenceDiagnosis"]:
        widget = getattr(self, "diag_text", None)
        if widget is None or not getattr(widget, "winfo_exists", lambda: 0)():
            return None
        try:
            idx = widget.index(f"@{event.x},{event.y}")
            tags = widget.tag_names(idx)
        except Exception:
            return None
        for tag in tags:
            if isinstance(tag, str) and tag.startswith("diag_item_"):
                try:
                    d = getattr(self, "_diag_tag_map", {}).get(tag, None)
                except Exception:
                    d = None
                if d is not None:
                    return d
        return None

    def _on_diag_text_right_click(self, event):
        widget = getattr(self, "diag_text", None)
        if widget is None or not getattr(widget, "winfo_exists", lambda: 0)():
            return

        diag = self._diag_from_text_event(event)

        try:
            has_sel = True
            try:
                _ = widget.selection_get()
            except tk.TclError:
                has_sel = False

            menu = tk.Menu(
                self.root,
                tearoff=0,
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_PRIMARY,
                activebackground=Theme.PRIMARY,
                activeforeground="white",
            )
            menu.add_command(
                label=t("menu.copy"),
                command=lambda: widget.event_generate("<<Copy>>"),
                state=tk.NORMAL if has_sel else tk.DISABLED,
            )

            if diag is not None:
                menu.add_separator()
                menu.add_command(
                    label=t("menu.copy_sentence"),
                    command=lambda d=diag: self._copy_text((d.text or "").strip()),
                )
                menu.add_command(
                    label=t("menu.copy_diagnosis"),
                    command=lambda d=diag: self._copy_text(self._format_single_diagnosis(d)),
                )
                menu.add_command(
                    label=t("menu.copy_issues"),
                    command=lambda d=diag: self._copy_text("\n".join([iss.description for iss in (d.issues or []) if iss.description]).strip()),
                )
                menu.add_separator()
                has_sem_issue = any((iss.issue_type or "") == "semantic_outlier" for iss in (diag.issues or []))
                can_show_sem = bool(has_sem_issue and self.semantic_embedder is not None and self.corpus.semantic_index_exists())
                menu.add_command(
                    label=t("menu.show_similar_examples"),
                    command=lambda d=diag: self._show_semantic_examples(d),
                    state=tk.NORMAL if can_show_sem else tk.DISABLED,
                )
                menu.add_command(
                    label=t("menu.locate_in_results"),
                    command=lambda d=diag: self._locate_sentence_in_results(d),
                )

            menu.add_separator()
            menu.add_command(
                label=t("menu.select_all"),
                command=lambda: self._select_all_text(widget),
            )
            menu.add_command(
                label=t("menu.copy_all"),
                command=lambda: self._copy_text(widget.get("1.0", tk.END).rstrip()),
            )
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _show_diag_context_menu(self, event, diag: "SentenceDiagnosis"):
        try:
            menu = tk.Menu(
                self.root,
                tearoff=0,
                bg=Theme.BG_SECONDARY,
                fg=Theme.TEXT_PRIMARY,
                activebackground=Theme.PRIMARY,
                activeforeground="white",
            )
            menu.add_command(
                label=t("menu.copy_sentence"),
                command=lambda d=diag: self._copy_text((d.text or "").strip()),
            )
            menu.add_command(
                label=t("menu.copy_diagnosis"),
                command=lambda d=diag: self._copy_text(self._format_single_diagnosis(d)),
            )
            menu.add_command(
                label=t("menu.copy_issues"),
                command=lambda d=diag: self._copy_text("\n".join([iss.description for iss in (d.issues or []) if iss.description]).strip()),
            )
            menu.add_separator()
            has_sem_issue = any((iss.issue_type or "") == "semantic_outlier" for iss in (diag.issues or []))
            can_show_sem = bool(has_sem_issue and self.semantic_embedder is not None and self.corpus.semantic_index_exists())
            menu.add_command(
                label=t("menu.show_similar_examples"),
                command=lambda d=diag: self._show_semantic_examples(d),
                state=tk.NORMAL if can_show_sem else tk.DISABLED,
            )
            menu.add_command(
                label=t("menu.locate_in_results"),
                command=lambda d=diag: self._locate_sentence_in_results(d),
            )
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _locate_sentence_in_results(self, diag: "SentenceDiagnosis"):
        if not getattr(self, "result_text", None):
            return
        try:
            start_idx = f"1.0+{int(diag.start_pos)}c"
            end_idx = f"1.0+{int(diag.end_pos)}c"
            self.result_text.see(start_idx)
            try:
                self.result_text.tag_remove(tk.SEL, "1.0", tk.END)
                self.result_text.tag_add(tk.SEL, start_idx, end_idx)
                self.result_text.mark_set(tk.INSERT, start_idx)
            except Exception:
                pass
            try:
                self.result_text.focus_set()
            except Exception:
                pass
        except Exception:
            pass

    @staticmethod
    def _format_semantic_source(source: dict) -> str:
        """Format a semantic exemplar source into a short, user-friendly string."""
        if not isinstance(source, dict):
            return ""
        pdf = source.get("pdf", "") if isinstance(source.get("pdf", ""), str) else ""
        pdf = (pdf or "").strip()
        page = source.get("page", None)
        if isinstance(page, int) and page > 0 and pdf:
            return f"{pdf}#p{page}"
        return pdf

    def _show_semantic_examples(self, diag: "SentenceDiagnosis", top_k: int = 5):
        """Show top-k nearest corpus sentences for the selected sentence (semantic retrieval)."""
        if not self._require_semantic_model():
            return
        if not self._require_semantic_index():
            return

        sent = (getattr(diag, "text", "") or "").strip()
        clean = re.sub(r"\s+", " ", sent).strip()
        if not clean:
            return

        index = self._get_semantic_index()
        if index is None or not getattr(index, "sentences", None):
            messagebox.showerror(t("semantic.no_index_title"), t("semantic.no_index_message", path=self.corpus.library_path or ""))
            return

        try:
            vecs = self.semantic_embedder.embed([clean], batch_size=1)
        except Exception as e:
            messagebox.showerror(t("msg.error"), str(e))
            return
        if vecs is None or getattr(vecs, "shape", None) is None or len(vecs) < 1:
            return

        try:
            hits = index.query_topk(vecs[0], top_k=int(top_k or 5))
        except Exception:
            hits = []
        if not hits:
            messagebox.showinfo(t("msg.complete"), t("semantic.examples_empty"))
            return

        lines = [t("semantic.examples_header", sentence=clean), ""]
        for rank, (score, idx) in enumerate(hits, start=1):
            ex = (index.get_sentence(idx) or "").strip().replace("\n", " ")
            if len(ex) > 600:
                ex = ex[:599] + "…"
            src = self._format_semantic_source(index.get_source(idx))
            if src:
                lines.append(f"{rank}. {score:.3f}  [{src}] {ex}")
            else:
                lines.append(f"{rank}. {score:.3f}  {ex}")

        dialog = tk.Toplevel(self.root)
        dialog.title(t("semantic.examples_title"))
        dialog.geometry("860x520")
        dialog.configure(bg=Theme.BG_PRIMARY)

        container = tk.Frame(dialog, bg=Theme.BG_PRIMARY)
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)

        header = tk.Label(
            container,
            text=t("semantic.examples_title"),
            bg=Theme.BG_PRIMARY,
            fg=Theme.TEXT_PRIMARY,
            font=(FONT_UI, 12, "bold"),
            anchor="w",
        )
        header.pack(fill=tk.X, pady=(0, 10))

        text = tk.Text(
            container,
            wrap=tk.WORD,
            font=(FONT_MONO, 11),
            bg=Theme.BG_INPUT,
            fg=Theme.TEXT_PRIMARY,
            padx=12,
            pady=12,
            relief=tk.FLAT,
        )
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", "\n".join(lines).strip())
        try:
            self._install_text_context_menu(text)
            self._bind_readonly_text(text)
        except Exception:
            pass

    def _on_toggle_minor_issues(self):
        try:
            val = bool(self.show_minor_var.get())
        except Exception:
            val = True
        self._show_minor_issues = val
        try:
            self.settings.set("show_minor_issues", bool(val))
        except Exception:
            pass
        self._apply_diagnosis_filter()

    def _apply_diagnosis_filter(self):
        """Apply the 'show minor issues' toggle and refresh highlights + panel."""
        try:
            show_minor = bool(getattr(self, "_show_minor_issues", True))
        except Exception:
            show_minor = True

        all_diags = list(getattr(self, "_all_sentence_diagnoses", []) or [])
        if show_minor:
            self.last_sentence_diagnoses = all_diags
        else:
            filtered = []
            for d in all_diags:
                if any((iss.severity or "").lower() == "warning" for iss in (d.issues or [])):
                    filtered.append(d)
            self.last_sentence_diagnoses = filtered

        # Rebuild tooltip map and highlights for the currently displayed set.
        try:
            if getattr(self, "result_text", None) is not None:
                self.result_text.tag_remove("style_issue", "1.0", tk.END)
        except Exception:
            pass

        self._build_sentence_issue_map()
        self._highlight_problem_sentences()
        self.update_diagnosis_panel()
        self._update_diag_hint()

    def _update_diag_hint(self):
        hint = self._widgets.get("diag_hint")
        if hint is None or not getattr(hint, "winfo_exists", lambda: 0)():
            return

        analysis_ran = bool(getattr(self, "_analysis_ran", False))
        if not analysis_ran:
            hint.config(text=t("style.panel_hint"), fg=Theme.TEXT_MUTED)
            return

        total = len(getattr(self, "_all_sentence_diagnoses", []) or [])
        shown = len(getattr(self, "last_sentence_diagnoses", []) or [])

        if total <= 0:
            hint.config(text=t("style.no_issues"), fg=Theme.SUCCESS)
            return

        if shown <= 0 and not bool(getattr(self, "_show_minor_issues", True)):
            hint.config(text=t("style.no_severe_issues", total=total), fg=Theme.SUCCESS)
            return

        if shown == total:
            hint.config(text=t("style.summary", count=shown), fg=Theme.WARNING if shown > 0 else Theme.SUCCESS)
            return

        hint.config(text=t("style.summary_filtered", shown=shown, total=total), fg=Theme.WARNING if shown > 0 else Theme.SUCCESS)

    def _format_single_diagnosis(self, diag: SentenceDiagnosis) -> str:
        lines = [f"{t('style.sentence')} {diag.index + 1}: {diag.text.strip()}"]
        for issue in diag.issues:
            lines.append(f"- {issue.description}")
        return "\n".join(lines).strip()

    def copy_stats_to_clipboard(self):
        data = self.last_word_stats if self.stats_view == 'words' else self.last_phrase_stats
        if not data:
            self._flash_status(t("status.nothing_to_copy"), fg=Theme.WARNING)
            return

        primary_lang = self.last_weirdness.get('primary_lang', self.corpus.language)
        status_map = {
            'common': t("stats.common_short"),
            'normal': t("stats.normal_short"),
            'rare': t("stats.rare_short"),
            'unseen': t("stats.unseen_short")
        }

        if self.stats_view == 'phrases':
            header = [t("stats.phrase"), t("stats.doc_freq"), t("stats.doc_pct"), t("stats.count_in_text"), t("stats.status")]
            lines = ["\t".join(header)]
            for s in data:
                phrase = s.get("bigram", "")
                key = s.get("key", "")
                classification = self.corpus.classify_bigram(key, language=primary_lang) if key else "unseen"
                row = [
                    phrase,
                    f"{s.get('doc_freq', 0)}/{s.get('docs_total', 0)}",
                    f"{float(s.get('doc_percent', 0.0) or 0.0):.1f}%",
                    str(s.get("count_in_text", 0)),
                    status_map.get(classification, classification),
                ]
                lines.append("\t".join(row))
            self._copy_text("\n".join(lines))
            return

        header = [t("stats.word"), t("stats.doc_freq"), t("stats.doc_pct"), t("stats.total_freq"), t("stats.status")]
        lines = ["\t".join(header)]
        for s in data:
            word = s.get("word", "")
            classification = self.corpus.classify_word(word) if word else "unseen"
            row = [
                word,
                f"{s.get('doc_freq', 0)}/{s.get('docs_total', 0)}",
                f"{float(s.get('doc_percent', 0.0) or 0.0):.1f}%",
                str(s.get("total_freq", 0)),
                status_map.get(classification, classification),
            ]
            lines.append("\t".join(row))
        self._copy_text("\n".join(lines))

    def copy_diagnosis_to_clipboard(self):
        if not self.last_sentence_diagnoses:
            self._flash_status(t("status.nothing_to_copy"), fg=Theme.WARNING)
            return

        lines = []
        if self.last_weirdness:
            lines.append(t("domain.badge", score=int(self.last_weirdness.get("score", 0) or 0)))

        for diag in self.last_sentence_diagnoses:
            header = f"{t('style.sentence')} {diag.index + 1}: {diag.text.strip()}"
            lines.append(header)
            for issue in diag.issues:
                lines.append(f"- {issue.description}")
            lines.append("")

        self._copy_text("\n".join(lines).strip())

    def on_stats_view_change(self, event=None):
        selected_label = self.stats_view_var.get()
        view = self._stats_view_reverse.get(selected_label, 'words')
        self._set_stats_view(view)

    def _set_stats_view(self, view: str):
        if view not in ('words', 'phrases'):
            view = 'words'
        self.stats_view = view
        self._apply_stats_view_headers()
        self.update_stats_table()
        self._update_stats_hint()

    def _apply_stats_view_headers(self):
        if not hasattr(self, '_table_headers') or not self._table_headers:
            return
        header_keys = ["stats.word", "stats.doc_freq", "stats.doc_pct", "stats.total_freq", "stats.status"]
        if self.stats_view == 'phrases':
            header_keys = ["stats.phrase", "stats.doc_freq", "stats.doc_pct", "stats.count_in_text", "stats.status"]
        labels = [lbl for lbl, _key in self._table_headers]
        self._table_headers = list(zip(labels, header_keys))
        for lbl, key in self._table_headers:
            lbl.config(text=t(key))

    def _update_stats_hint(self):
        if not self._widgets.get('stats_hint'):
            return

        if self.stats_view == 'phrases':
            data = self.last_phrase_stats
            if not data:
                self._widgets['stats_hint'].config(text=t("stats.panel_hint_phrases"), fg=Theme.TEXT_MUTED)
                return
            unseen = sum(1 for s in data if float(s.get('doc_percent', 0.0) or 0.0) == 0.0)
            rare = sum(1 for s in data if 0.0 < float(s.get('doc_percent', 0.0) or 0.0) < 10.0)
            self._widgets['stats_hint'].config(
                text=t("stats.phrase_summary", total=len(data), unseen=unseen, rare=rare),
                fg=Theme.DANGER if unseen > 0 else Theme.SUCCESS
            )
            return

        # words
        data = self.last_word_stats
        if not data:
            self._widgets['stats_hint'].config(text=t("stats.panel_hint"), fg=Theme.TEXT_MUTED)
            return
        unseen = sum(1 for s in data if float(s.get('doc_percent', 0.0) or 0.0) == 0.0)
        rare = sum(1 for s in data if 0.0 < float(s.get('doc_percent', 0.0) or 0.0) < 10.0)
        self._widgets['stats_hint'].config(
            text=t("stats.summary", total=len(data), unseen=unseen, rare=rare),
            fg=Theme.DANGER if unseen > 0 else Theme.SUCCESS
        )

    def _update_weirdness_badge(self):
        badge = self._widgets.get('weirdness_badge')
        if not badge:
            return
        if not self.last_weirdness:
            badge.config(
                text=t("domain.badge_placeholder"),
                fg=Theme.TEXT_MUTED,
                bg=Theme.BG_TERTIARY
            )
            if hasattr(self, "weirdness_tooltip") and self.weirdness_tooltip:
                self.weirdness_tooltip.text = t("domain.tooltip.placeholder")
            return
        score = int(self.last_weirdness.get('score', 0) or 0)
        level = self.last_weirdness.get('level', 'low')
        if level == 'high':
            color = Theme.DANGER
        elif level == 'medium':
            color = Theme.WARNING
        else:
            color = Theme.SUCCESS

        badge.config(
            text=t("domain.badge", score=score),
            fg=color,
            bg=Theme.BG_TERTIARY
        )

        # Tooltip content (simple breakdown)
        lines = [
            t("domain.tooltip.score", score=score),
            t("domain.tooltip.words", unseen=self.last_weirdness.get('word_unseen_ratio', 0.0),
              rare=self.last_weirdness.get('word_rare_ratio', 0.0)),
        ]
        if self.last_weirdness.get('phrase_available', False):
            lines.append(t("domain.tooltip.phrases", unseen=self.last_weirdness.get('bigram_unseen_ratio', 0.0)))
        lines.append(t("domain.tooltip.sentences", ratio=self.last_weirdness.get('outlier_ratio', 0.0)))
        if self.semantic_embedder is None:
            lines.append(t("domain.tooltip.semantic_disabled"))
        elif not self.corpus.semantic_index_exists():
            lines.append(t("domain.tooltip.semantic_no_index"))
        else:
            lines.append(t("domain.tooltip.semantic", ratio=self.last_weirdness.get('semantic_ratio', 0.0)))

        if self.last_weirdness.get("syntax_available", False):
            lines.append(t("domain.tooltip.syntax", ratio=self.last_weirdness.get("syntax_ratio", 0.0)))
        else:
            lines.append(t("domain.tooltip.syntax_disabled"))
        lines.append(t("domain.tooltip.style", ratio=self.last_weirdness.get('style_ratio', 0.0)))
        if hasattr(self, "weirdness_tooltip") and self.weirdness_tooltip:
            self.weirdness_tooltip.text = "\n".join(lines)

    def refresh_ui(self):
        self.root.title(f"{t('app.title')} v{VERSION}")
        self._widgets['title_label'].config(text=t('app.title'))
        self._widgets['btn_load_pdf'].set_text(t("toolbar.load_pdf"))
        self._widgets['btn_show_vocab'].set_text(t("toolbar.show_vocab"))
        self._widgets['btn_detect'].set_text(t("panel.analyze"))
        self._widgets['input_title'].config(text=t("panel.input_title"))
        self._widgets['result_title'].config(text=t("panel.result_title"))
        self._widgets['stats_title'].config(text=t("stats.panel_title"))
        if self._widgets.get('btn_copy_stats'):
            self._widgets['btn_copy_stats'].set_text(t("btn.copy_table"))
        if self._widgets.get('btn_copy_diag'):
            self._widgets['btn_copy_diag'].set_text(t("btn.copy_diagnosis"))
        if self._widgets.get('tt_copy_stats'):
            self._widgets['tt_copy_stats'].text = t("tooltip.copy_stats")
        if self._widgets.get('tt_copy_diag'):
            self._widgets['tt_copy_diag'].text = t("tooltip.copy_diagnosis")

        # Update stats view dropdown labels for the new language
        self._stats_view_labels = {
            'words': t("stats.view.words"),
            'phrases': t("stats.view.phrases"),
        }
        self._stats_view_reverse = {v: k for k, v in self._stats_view_labels.items()}
        self._widgets['stats_view_dropdown']['values'] = list(self._stats_view_reverse.keys())
        self.stats_view_var.set(self._stats_view_labels.get(self.stats_view, t("stats.view.words")))

        self._apply_stats_view_headers()
        self._update_stats_hint()

        placeholder_key = "stats.placeholder" if self.stats_view == 'words' else "stats.placeholder_phrases"
        stats_ph = self._widgets.get('stats_placeholder')
        if stats_ph is not None and getattr(stats_ph, "winfo_exists", lambda: 0)():
            stats_ph.config(text=t(placeholder_key))
        self._widgets['diag_title'].config(text=t("style.panel_title"))
        self.update_diagnosis_panel()
        self._update_diag_hint()

        # Update legend labels
        for lbl, key in self._legend_labels:
            lbl.config(text=t(key))

        # Update table header labels
        for lbl, key in self._table_headers:
            lbl.config(text=t(key))

        # Refresh stats table if data exists
        if self.last_word_stats or self.last_phrase_stats:
            self.update_stats_table()

        self._update_weirdness_badge()

        if self.corpus.doc_count > 0:
            self.status_label.config(
                text=t("status.ready",
                      pdf_count=self.corpus.doc_count,
                      word_count=f"{len(self.corpus.word_doc_freq):,}"),
                fg=Theme.SUCCESS
            )

        # Progress panel (if visible)
        if self._widgets.get('btn_cancel_task'):
            if self._busy and self._cancel_event is not None and getattr(self._cancel_event, "is_set", lambda: False)():
                self._widgets['btn_cancel_task'].set_text(t("progress.btn.canceling"))
                if hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                    self._widgets['btn_cancel_task'].set_enabled(False)
            else:
                self._widgets['btn_cancel_task'].set_text(t("progress.btn.cancel"))
                if hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                    self._widgets['btn_cancel_task'].set_enabled(True)

        if self._busy:
            try:
                self._widgets['progress_title'].config(text=t("progress.build.title"))
            except Exception:
                pass
            # Re-render current progress texts in the new language
            self._update_task_progress_ui(self._stage_name, self._stage_last_done, self._stage_last_total, self._stage_last_detail)

    def increase_font(self):
        if self.font_size < 18:
            self.font_size += 1
            self.settings.set('font_size', self.font_size)
            self.update_font_size()

    def decrease_font(self):
        if self.font_size > 9:
            self.font_size -= 1
            self.settings.set('font_size', self.font_size)
            self.update_font_size()

    def update_font_size(self):
        self.font_label.config(text=str(self.font_size))
        self.input_text.config(font=(FONT_MONO, self.font_size))
        self.result_text.config(font=(FONT_MONO, self.font_size))
        for tag in ["rare", "unseen"]:
            self.result_text.tag_configure(tag, font=(FONT_MONO, self.font_size, "bold"))
        self.result_text.tag_configure("common", font=(FONT_MONO, self.font_size))
        self.result_text.tag_configure("normal", font=(FONT_MONO, self.font_size))
        try:
            self.update_diagnosis_panel()
            self._update_diag_hint()
        except Exception:
            pass

    def toggle_theme(self):
        """Toggle between light and dark theme"""
        if self._busy:
            messagebox.showwarning(t("msg.warning"), t("progress.busy_message"))
            return
        self.dark_mode = not self.dark_mode
        self.settings.set('dark_mode', self.dark_mode)
        Theme.set_mode(self.dark_mode)

        # Save current input text
        input_content = self.input_text.get("1.0", tk.END)

        # Destroy all widgets and rebuild UI
        for widget in self.root.winfo_children():
            widget.destroy()

        self._widgets = {}
        self.create_ui()

        # Restore input text
        self.input_text.insert("1.0", input_content.strip())

        # Update status
        self.load_vocabulary()

    def update_library_dropdown(self):
        """Update library dropdown with available libraries"""
        libraries = self.library_manager.list_libraries()
        lib_names = [lib['name'] for lib in libraries]
        self._widgets['lib_dropdown']['values'] = lib_names
        if self.current_library and self.current_library in lib_names:
            self.library_var.set(self.current_library)
        elif lib_names:
            self.library_var.set(lib_names[0])
        else:
            self.library_var.set("")

    def on_library_change(self, event=None):
        """Handle library selection change"""
        selected = self.library_var.get()
        if selected and selected != self.current_library:
            self.current_library = selected
            self.settings.set('current_library', selected)
            library_path = self.library_manager.get_library_path(selected)
            self.corpus = AcademicCorpus(library_path)
            self.load_vocabulary()
            self.last_word_stats = []
            self.update_stats_table()

    def create_new_library(self):
        """Create a new library"""
        from tkinter import simpledialog
        name = simpledialog.askstring(
            t("library.new_title"),
            t("library.new_prompt"),
            parent=self.root
        )
        if name:
            name = name.strip()
            if name and not self.library_manager.library_exists(name):
                path = self.library_manager.create_library(name)
                self.current_library = name
                self.settings.set('current_library', name)
                self.corpus = AcademicCorpus(path)
                self.update_library_dropdown()
                self.load_vocabulary()
                self.last_word_stats = []
                self.update_stats_table()
            elif self.library_manager.library_exists(name):
                messagebox.showwarning(t("msg.warning"), t("library.exists"))

    def delete_current_library(self):
        """Delete the currently selected library"""
        if not self.current_library:
            return
        if messagebox.askyesno(t("library.delete_title"),
                              t("library.delete_confirm", name=self.current_library)):
            self.library_manager.delete_library(self.current_library)
            self.current_library = None
            self.settings.set('current_library', None)
            self.corpus = AcademicCorpus(None)
            self.update_library_dropdown()
            # Select first available library if any
            libraries = self.library_manager.list_libraries()
            if libraries:
                self.on_library_change()
            else:
                self.load_vocabulary()
                self.last_word_stats = []
                self.update_stats_table()

    def show_library_menu(self):
        """Show library management popup menu"""
        menu = tk.Menu(self.root, tearoff=0, font=(FONT_UI, 10),
                      bg=Theme.BG_SECONDARY, fg=Theme.TEXT_PRIMARY,
                      activebackground=Theme.PRIMARY, activeforeground="white")

        has_library = bool(self.current_library)

        menu.add_command(label=t("library.menu_rename"),
                        command=self.rename_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_info"),
                        command=self.show_library_info,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_open_folder"),
                        command=self.open_library_folder)
        menu.add_separator()
        menu.add_command(label=t("library.menu_clear"),
                        command=self.clear_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)
        menu.add_command(label=t("library.menu_delete"),
                        command=self.delete_current_library,
                        state=tk.NORMAL if has_library else tk.DISABLED)

        # Show menu at button position
        btn = self._widgets['btn_lib_menu']
        x = btn.winfo_rootx()
        y = btn.winfo_rooty() + btn.winfo_height()
        menu.post(x, y)

    def rename_current_library(self):
        """Rename the current library"""
        if not self.current_library:
            return
        from tkinter import simpledialog
        new_name = simpledialog.askstring(
            t("library.rename_title"),
            t("library.rename_prompt"),
            initialvalue=self.current_library,
            parent=self.root
        )
        if new_name and new_name.strip() and new_name.strip() != self.current_library:
            new_name = new_name.strip()
            if self.library_manager.library_exists(new_name):
                messagebox.showwarning(t("msg.warning"), t("library.exists"))
                return
            if self.library_manager.rename_library(self.current_library, new_name):
                self.current_library = new_name
                self.settings.set('current_library', new_name)
                self.corpus.library_path = self.library_manager.get_library_path(new_name)
                self.update_library_dropdown()

    def show_library_info(self):
        """Show library information dialog"""
        if not self.current_library:
            return
        info = self.library_manager.get_library_info(self.current_library)
        lib_path = info.get('path', '')
        base = os.path.splitext(lib_path)[0] if lib_path else ""
        has_semantic_index = bool(base) and os.path.exists(base + ".sentences.json") and os.path.exists(base + ".embeddings.npy")
        semantic_status = t("semantic.index_ready") if has_semantic_index else t("semantic.index_missing")
        msg = t("library.info_message",
               name=info['name'],
               doc_count=info['doc_count'],
               word_count=f"{info['word_count']:,}",
               semantic=semantic_status,
               path=info['path'])
        messagebox.showinfo(t("library.info_title"), msg)

    def open_library_folder(self):
        """Open the libraries folder in file explorer"""
        folder = self.library_manager.libraries_dir
        if os.path.exists(folder):
            os.startfile(folder)
        else:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)

    def clear_current_library(self):
        """Clear all data in the current library"""
        if not self.current_library:
            return
        if messagebox.askyesno(t("library.clear_title"),
                              t("library.clear_confirm", name=self.current_library)):
            self.library_manager.clear_library(self.current_library)
            self.corpus = AcademicCorpus(self.library_manager.get_library_path(self.current_library))
            self.load_vocabulary()
            self.last_word_stats = []
            self.update_stats_table()

    def load_vocabulary(self):
        if self.corpus.load_vocabulary():
            self.status_label.config(
                text=t("status.ready",
                      pdf_count=self.corpus.doc_count,
                      word_count=f"{len(self.corpus.word_doc_freq):,}"),
                fg=Theme.SUCCESS
            )
            # Clear onboarding text if present
            current_text = self.input_text.get("1.0", tk.END).strip()
            if current_text.startswith("📚") or current_text.startswith("Welcome"):
                self.input_text.delete("1.0", tk.END)
        else:
            # Show onboarding guidance for first-time users
            if not self.current_library:
                self.status_label.config(text=t("status.not_found"), fg=Theme.WARNING)
                self.show_onboarding()
            else:
                self.status_label.config(
                    text=t("status.ready", pdf_count=0, word_count="0"),
                    fg=Theme.WARNING
                )

    def show_onboarding(self):
        """Show onboarding instructions in input text area"""
        self.input_text.delete("1.0", tk.END)
        onboarding = t("onboarding.text")
        self.input_text.insert("1.0", onboarding)
        self.input_text.config(fg=Theme.TEXT_MUTED)

    @staticmethod
    def _format_clock(seconds: float) -> str:
        try:
            s = max(0, int(round(float(seconds or 0.0))))
        except Exception:
            s = 0
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h > 0:
            return f"{h:d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    def _show_progress_panel(self):
        if getattr(self, "_progress_panel", None) is None:
            return
        try:
            # Keep it pinned under the toolbar.
            if not self._progress_panel.winfo_ismapped():
                opts = dict(getattr(self, "_progress_panel_pack_opts", {}) or {})
                # If we hid it via pack_forget, re-packing would append it to the end.
                # Insert it back above the main split so it stays under the toolbar.
                main_split = getattr(self, "_main_split", None)
                if main_split is not None and getattr(main_split, "winfo_exists", lambda: 0)():
                    opts["before"] = main_split
                self._progress_panel.pack(**opts)
        except Exception:
            pass

    def _hide_progress_panel(self):
        if getattr(self, "_progress_panel", None) is None:
            return
        try:
            # Keep the panel visible to reduce anxiety; reset to an idle state instead of hiding.
            if self._widgets.get('progress_title'):
                self._widgets['progress_title'].config(text=t("progress.idle_title"))
            if self._widgets.get('progress_stage'):
                self._widgets['progress_stage'].config(text=t("progress.idle_stage"))
            if self._widgets.get('progress_meta'):
                self._widgets['progress_meta'].config(text="")
            if self._widgets.get('progress_detail'):
                self._widgets['progress_detail'].config(text="")
            if self._widgets.get('progress_percent'):
                self._widgets['progress_percent'].config(text="")
            if self._widgets.get('progress_note'):
                self._widgets['progress_note'].config(text=t("progress.idle_note"))
            if self._widgets.get('progress_bar'):
                self._widgets['progress_bar'].set_progress(0.0, animate=False)
            if self._widgets.get('btn_cancel_task') and hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                self._widgets['btn_cancel_task'].set_enabled(False)
        except Exception:
            pass

    def _start_task_ui(self, task_name: str):
        self._busy = True
        self._task_name = task_name or ""
        self._task_start_time = time.monotonic()
        self._stage_name = ""
        self._stage_start_time = self._task_start_time
        self._stage_last_update_time = self._task_start_time
        self._stage_last_done = 0
        self._stage_last_total = 0
        self._stage_last_detail = ""
        self._stage_speed_ema = None

        # Reset visuals
        try:
            title_key = "progress.build.title"
            if self._task_name == "rebuild_semantic":
                title_key = "progress.rebuild_semantic.title"
            self._widgets['progress_title'].config(text=t(title_key))
            self._widgets['progress_stage'].config(text="")
            self._widgets['progress_meta'].config(text="")
            self._widgets['progress_detail'].config(text="")
            self._widgets['progress_percent'].config(text="")
            self._widgets['progress_note'].config(text="")
            if self._widgets.get('progress_bar'):
                self._widgets['progress_bar'].set_progress(0.0, animate=False)
            if self._widgets.get('btn_cancel_task'):
                self._widgets['btn_cancel_task'].set_text(t("progress.btn.cancel"))
                self._widgets['btn_cancel_task'].command = self._cancel_current_task
                if hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                    self._widgets['btn_cancel_task'].set_enabled(True)
        except Exception:
            pass

        self._show_progress_panel()
        self._set_controls_enabled(False)

    def _finish_task_ui(self):
        self._busy = False
        self._task_name = ""
        self._cancel_event = None
        self._busy_thread = None
        self._set_controls_enabled(True)
        self._hide_progress_panel()

    def _set_controls_enabled(self, enabled: bool):
        enabled = bool(enabled)
        try:
            # ModernButton instances
            for key in ('btn_load_pdf', 'btn_show_vocab', 'btn_detect', 'btn_new_lib', 'btn_lib_menu'):
                w = self._widgets.get(key)
                if w is not None and hasattr(w, "set_enabled"):
                    w.set_enabled(enabled)

            # IconButton instances
            for key in ('btn_theme', 'btn_font_minus', 'btn_font_plus'):
                w = self._widgets.get(key)
                if w is not None and hasattr(w, "set_enabled"):
                    w.set_enabled(enabled)

            # ttk Comboboxes
            lib_dd = self._widgets.get('lib_dropdown')
            if lib_dd is not None:
                lib_dd.configure(state='readonly' if enabled else 'disabled')
            lang_dd = self._widgets.get('lang_dropdown')
            if lang_dd is not None:
                lang_dd.configure(state='readonly' if enabled else 'disabled')

            stats_dd = self._widgets.get('stats_view_dropdown')
            if stats_dd is not None:
                stats_dd.configure(state='readonly' if enabled else 'disabled')
        except Exception:
            pass

    def _update_task_progress_ui(self, stage: str, done: int, total: int, detail: str = ""):
        try:
            done_i = max(0, int(done or 0))
            total_i = max(0, int(total or 0))
        except Exception:
            done_i, total_i = 0, 0

        # Stage change resets ETA estimation.
        stage = stage or ""
        now = time.monotonic()
        if stage != self._stage_name:
            self._stage_name = stage
            self._stage_start_time = now
            self._stage_last_update_time = now
            self._stage_last_done = 0
            self._stage_speed_ema = None

        # Update speed estimate (EMA).
        delta_done = max(0, done_i - int(self._stage_last_done or 0))
        delta_t = max(1e-6, now - float(self._stage_last_update_time or now))
        inst_speed = (delta_done / delta_t) if delta_done > 0 else 0.0
        if inst_speed > 0:
            if self._stage_speed_ema is None:
                self._stage_speed_ema = inst_speed
            else:
                self._stage_speed_ema = 0.25 * inst_speed + 0.75 * float(self._stage_speed_ema)
        self._stage_last_done = done_i
        self._stage_last_update_time = now

        frac = (done_i / total_i) if total_i > 0 else 0.0
        frac = max(0.0, min(1.0, float(frac)))
        pct = int(round(frac * 100))

        self._stage_last_total = total_i
        self._stage_last_detail = detail or ""

        elapsed = now - float(self._stage_start_time or now)
        eta_s = None
        speed = float(self._stage_speed_ema or 0.0)
        if total_i > 0 and done_i > 0 and speed > 1e-6 and done_i < total_i:
            eta_s = (total_i - done_i) / speed

        # Text (localized)
        if stage == "extract":
            stage_text = t("progress.build.step_extract")
            note_text = ""
            filename = (detail or "").strip().replace("\n", " ")
            if len(filename) > 48:
                filename = filename[:47] + "…"
            if filename:
                detail_text = t("progress.build.detail_pdf", current=done_i, total=total_i, filename=filename)
            else:
                detail_text = t("progress.build.detail_pdf_count", current=done_i, total=total_i)
        elif stage == "embed":
            stage_text = t("progress.build.step_embed")
            note_text = t("progress.note.embedding")
            detail_text = t("progress.build.detail_embed", current=done_i, total=total_i)
        elif stage == "syntax":
            stage_text = t("progress.build.step_syntax")
            note_text = t("progress.note.syntax")
            detail_text = t("progress.build.detail_syntax", current=done_i, total=total_i)
        else:
            stage_text = ""
            note_text = ""
            detail_text = ""

        meta_parts = [t("progress.meta.elapsed", elapsed=self._format_clock(elapsed))]
        if total_i > 0:
            if eta_s is not None:
                meta_parts.append(t("progress.meta.eta", eta=self._format_clock(eta_s)))
            else:
                meta_parts.append(t("progress.meta.eta_unknown"))
        meta_text = " • ".join([p for p in meta_parts if p])

        try:
            self._widgets['progress_stage'].config(text=stage_text)
            self._widgets['progress_meta'].config(text=meta_text)
            self._widgets['progress_detail'].config(text=detail_text)
            self._widgets['progress_percent'].config(text=f"{pct}%")
            self._widgets['progress_note'].config(text=note_text)
            if self._widgets.get('progress_bar'):
                bar = self._widgets['progress_bar']
                want_ind = bool(total_i > 0 and done_i <= 0 and stage in ("extract", "embed", "syntax"))
                if hasattr(bar, "set_indeterminate"):
                    bar.set_indeterminate(want_ind)
                bar.set_progress(frac, animate=not want_ind)
        except Exception:
            pass

    def _on_window_close(self):
        if self._busy:
            try:
                ok = messagebox.askyesno(t("msg.warning"), t("exit.confirm_busy"))
            except Exception:
                ok = True
            if not ok:
                return
            try:
                self._cancel_current_task()
            except Exception:
                pass
            try:
                self.root.after(120, self.root.destroy)
            except Exception:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            return

        try:
            self.root.destroy()
        except Exception:
            pass

    def _cancel_current_task(self):
        if not self._busy or not self._cancel_event:
            return
        try:
            self._cancel_event.set()
        except Exception:
            pass
        try:
            # Update UI immediately to reduce anxiety.
            if self._widgets.get('btn_cancel_task'):
                self._widgets['btn_cancel_task'].set_text(t("progress.btn.canceling"))
                self._widgets['btn_cancel_task'].command = None
                if hasattr(self._widgets['btn_cancel_task'], "set_enabled"):
                    self._widgets['btn_cancel_task'].set_enabled(False)
            self._widgets['progress_stage'].config(text=t("progress.stage.canceling"))
        except Exception:
            pass

    def select_pdf_folder(self):
        if self._busy:
            messagebox.showwarning(t("msg.warning"), t("progress.busy_message"))
            return

        # Check if a library is selected first
        if not self.current_library:
            if messagebox.askyesno(t("library.new_title"), t("library.create_first")):
                self.create_new_library()
                if not self.current_library:
                    return  # User cancelled creating library
            else:
                return

        if not self._require_semantic_model():
            return

        folder = filedialog.askdirectory(title=t("msg.select_folder"))
        if not folder:
            return

        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event

        # Show progress immediately (main thread)
        self._start_task_ui("build_library")
        try:
            total_pdfs = len(list(Path(folder).rglob("*.pdf")))
        except Exception:
            total_pdfs = 0
        self._update_task_progress_ui("extract", 0, total_pdfs, "")

        def process():
            def ui(fn):
                try:
                    self.root.after(0, fn)
                except Exception:
                    pass

            def ui_set_status(message: str, fg: str):
                ui(lambda m=message, c=fg: self.status_label.config(text=m, fg=c))

            def progress_callback(current, total, filename):
                safe_name = (filename or "").strip().replace("\n", " ")
                short_name = safe_name
                if len(short_name) > 32:
                    short_name = "…" + short_name[-31:]
                ui_set_status(
                    t("status.processing", current=current, total=total, filename=short_name),
                    Theme.WARNING,
                )
                ui(lambda c=current, tt=total, f=filename: self._update_task_progress_ui("extract", c, tt, f))

            def semantic_progress(current, total, _stage):
                ui_set_status(
                    t("status.embedding", current=current, total=total),
                    Theme.WARNING,
                )
                ui(lambda c=current, tt=total: self._update_task_progress_ui("embed", c, tt, ""))

            def syntax_progress(current, total, _stage):
                ui_set_status(
                    t("status.syntax", current=current, total=total),
                    Theme.WARNING,
                )
                ui(lambda c=current, tt=total: self._update_task_progress_ui("syntax", c, tt, ""))

            try:
                ui_set_status(t("status.starting"), Theme.WARNING)
                count = self.corpus.process_pdf_folder(
                    folder,
                    progress_callback,
                    semantic_embedder=self.semantic_embedder,
                    semantic_progress_callback=semantic_progress,
                    syntax_analyzer=self.syntax_analyzer,
                    syntax_progress_callback=syntax_progress if self.syntax_analyzer is not None else None,
                    cancel_event=cancel_event,
                )
                if cancel_event.is_set():
                    raise CancelledError()

                self.corpus.save_vocabulary()
                # Reset semantic index cache
                self.semantic_index = None
                self.semantic_index_library_path = None

                ui_set_status(
                    t("status.complete", count=count, word_count=f"{len(self.corpus.word_doc_freq):,}"),
                    Theme.SUCCESS,
                )
                ui(lambda: self._finish_task_ui())
                ui(lambda: messagebox.showinfo(t("msg.complete"), t("msg.success_process", count=count)))
            except CancelledError:
                try:
                    # Restore previous state from disk to avoid partial / duplicated counts.
                    self.corpus.load_vocabulary()
                except Exception:
                    pass
                ui_set_status(t("progress.canceled"), Theme.WARNING)
                ui(lambda: self._finish_task_ui())
            except Exception as e:
                ui_set_status(t("progress.failed"), Theme.DANGER)
                ui(lambda err=str(e): messagebox.showerror(t("msg.error"), err))
                ui(lambda: self._finish_task_ui())

        self._busy_thread = threading.Thread(target=process, daemon=True)
        self._busy_thread.start()

    def show_vocabulary(self):
        if self.corpus.doc_count == 0:
            messagebox.showwarning(t("msg.warning"), t("msg.load_vocab_first"))
            return

        words = self.corpus.get_common_words(300)
        content = t("vocab.header", doc_count=self.corpus.doc_count)
        content += "\n\n"

        for word, doc_freq in words[:100]:
            pct = doc_freq / self.corpus.doc_count * 100
            content += f"{word}: {doc_freq}/{self.corpus.doc_count} ({pct:.0f}%)\n"

        dialog = tk.Toplevel(self.root)
        dialog.title(t("toolbar.show_vocab"))
        dialog.geometry("550x450")
        dialog.configure(bg=Theme.BG_PRIMARY)

        text = tk.Text(dialog, wrap=tk.WORD, font=(FONT_MONO, 11),
                      bg=Theme.BG_INPUT, fg=Theme.TEXT_PRIMARY, padx=12, pady=12,
                      relief=tk.FLAT)
        text.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        text.insert("1.0", content)

    def update_stats_table(self):
        """Update the statistics table (words or phrases) sorted by rarity"""
        # Clear existing rows
        for widget in self.stats_frame.winfo_children():
            widget.destroy()

        data = self.last_word_stats if self.stats_view == 'words' else self.last_phrase_stats
        placeholder_key = "stats.placeholder" if self.stats_view == 'words' else "stats.placeholder_phrases"

        if not data:
            self._widgets['stats_placeholder'] = tk.Label(self.stats_frame,
                    text=t(placeholder_key),
                    font=(FONT_UI, 11),
                    bg=Theme.BG_INPUT, fg=Theme.TEXT_MUTED)
            self._widgets['stats_placeholder'].pack(pady=30)
            return

        col_widths = [20, 14, 12, 14, 14]
        color_map = {
            'common': Theme.SUCCESS,
            'normal': Theme.NORMAL_COLOR,
            'rare': Theme.WARNING,
            'unseen': Theme.DANGER
        }
        status_map = {
            'common': t("stats.common_short"),
            'normal': t("stats.normal_short"),
            'rare': t("stats.rare_short"),
            'unseen': t("stats.unseen_short")
        }

        primary_lang = self.last_weirdness.get('primary_lang', self.corpus.language)

        for i, stats in enumerate(data):
            row_bg = Theme.BG_INPUT if i % 2 == 0 else Theme.BG_SECONDARY
            row = tk.Frame(self.stats_frame, bg=row_bg)
            row.pack(fill=tk.X)
            row.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            if self.stats_view == 'words':
                term_text = stats.get('word', '')
                classification = self.corpus.classify_word(term_text)
                col4_text = str(stats.get('total_freq', 0))
            else:
                term_text = stats.get('bigram', '')
                key = stats.get('key', '')
                classification = self.corpus.classify_bigram(key, language=primary_lang) if key else 'unseen'
                col4_text = str(stats.get('count_in_text', 0))
            color = color_map.get(classification, Theme.TEXT_PRIMARY)

            # Term (word/phrase)
            display_term = term_text
            if len(display_term) > 34:
                display_term = display_term[:33] + "…"
            lbl = tk.Label(row, text=display_term, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[0], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.config(cursor="hand2")
            lbl.bind("<Button-1>", lambda e, txt=term_text: self._copy_text(txt))
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
            if display_term != term_text:
                try:
                    setattr(lbl, "_tooltip", ToolTip(lbl, text=term_text))
                except Exception:
                    pass

            # Doc frequency
            doc_freq_text = f"{stats.get('doc_freq', 0)}/{stats.get('docs_total', 0)}"
            lbl = tk.Label(row, text=doc_freq_text, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[1], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Doc percentage
            pct_text = f"{float(stats.get('doc_percent', 0.0) or 0.0):.1f}%"
            lbl = tk.Label(row, text=pct_text, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[2], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Total frequency / Count in text
            lbl = tk.Label(row, text=col4_text, font=(FONT_MONO, 11),
                    bg=row_bg, fg=color, width=col_widths[3], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

            # Status
            lbl = tk.Label(row, text=status_map.get(classification, ''), font=(FONT_UI, 10),
                    bg=row_bg, fg=color, width=col_widths[4], anchor='w')
            lbl.pack(side=tk.LEFT, padx=10, pady=6)
            lbl.bind("<MouseWheel>", lambda e: self.stats_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    def analyze_text(self):
        raw_text = self.input_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showwarning(t("msg.warning"), t("msg.enter_text"))
            return

        if self.corpus.doc_count == 0:
            messagebox.showwarning(t("msg.warning"), t("msg.load_vocab_first"))
            return

        if not self._require_semantic_model():
            return
        if not self._require_semantic_index():
            return

        # Normalize soft line breaks for sentence-level analysis to reduce false "too short" alarms.
        analysis_text = normalize_soft_line_breaks_preserve_len(raw_text)

        self.detected_language = LanguageDetector.detect(analysis_text)
        lang_display = {
            'en': t("lang.english"),
            'zh': t("lang.chinese"),
            'mixed': t("lang.mixed")
        }.get(self.detected_language, self.detected_language)

        self._widgets['lang_indicator'].config(
            text=t("lang.detected", lang=lang_display)
        )

        self.result_text.config(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)

        primary_lang = self._primary_domain_language(analysis_text, self.detected_language)

        # Update style analyzer thresholds using corpus statistics (if available)
        if self.style_analyzer:
            baseline = self.corpus.get_sentence_length_baseline(primary_lang)
            if baseline.get('mean', 0) > 0:
                self.style_analyzer.set_corpus_stats(baseline['mean'], primary_lang)

        # Split into segments while preserving original text exactly
        segments = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+|\s+|[^\s]', raw_text)

        word_stats_dict = {}
        total_words = 0
        stats_by_type = {'common': 0, 'normal': 0, 'rare': 0, 'unseen': 0}

        for seg in segments:
            if seg.isspace():
                self.result_text.insert(tk.END, seg, "normal")
                continue

            # English token
            if re.fullmatch(r'[a-zA-Z]+', seg):
                lower_word = seg.lower()
                if lower_word in STOP_WORDS or len(lower_word) < 3:
                    self.result_text.insert(tk.END, seg, "normal")
                    continue
                # Only classify when corpus has English docs
                if self.corpus.doc_count_by_lang.get('en', 0) == 0 and self.corpus.doc_count > 0:
                    self.result_text.insert(tk.END, seg, "normal")
                    continue

                total_words += 1
                classification = self.corpus.classify_word(lower_word)
                stats_by_type[classification] += 1
                if lower_word not in word_stats_dict:
                    word_stats_dict[lower_word] = self.corpus.get_word_stats(lower_word)
                self.result_text.insert(tk.END, seg, classification)
                continue

            # Chinese sequence -> jieba segmentation
            if re.fullmatch(r'[\u4e00-\u9fff]+', seg):
                zh_tokens = jieba.lcut(seg, cut_all=False) if jieba else list(seg)
                for tok in zh_tokens:
                    if not tok:
                        continue
                    if len(tok) < 2 or tok in STOP_WORDS_ZH:
                        self.result_text.insert(tk.END, tok, "normal")
                        continue
                    # Only classify when corpus has Chinese docs
                    if self.corpus.doc_count_by_lang.get('zh', 0) == 0 and self.corpus.doc_count > 0:
                        self.result_text.insert(tk.END, tok, "normal")
                        continue

                    total_words += 1
                    classification = self.corpus.classify_word(tok)
                    stats_by_type[classification] += 1
                    if tok not in word_stats_dict:
                        word_stats_dict[tok] = self.corpus.get_word_stats(tok)
                    self.result_text.insert(tk.END, tok, classification)
                continue

            # Other symbols/punctuation
            self.result_text.insert(tk.END, seg, "normal")

        # ========== Sentence Diagnosis (AI style + Domain weirdness) ==========
        if self.style_analyzer:
            sentences_with_pos = self.style_analyzer._split_sentences(analysis_text, self.detected_language)
        else:
            sentences_with_pos = self._split_sentences_with_positions(analysis_text, self.detected_language)

        all_diags = {
            idx: SentenceDiagnosis(index=idx, text=sent, start_pos=start, end_pos=end, issues=[])
            for idx, (sent, start, end) in enumerate(sentences_with_pos)
        }

        style_diags = self.style_analyzer.analyze_text(analysis_text, self.detected_language) if self.style_analyzer else []
        for sd in style_diags:
            if sd.index in all_diags:
                all_diags[sd.index].issues.extend(sd.issues)

        domain_issues, domain_metrics, phrase_counter = self._analyze_domain_sentences(
            sentences_with_pos, primary_lang, text=analysis_text
        )
        for idx, issues in domain_issues.items():
            if idx in all_diags:
                all_diags[idx].issues.extend(issues)

        semantic_issues, semantic_outlier_count = self._analyze_semantic_similarity(
            sentences_with_pos, primary_lang
        )
        domain_metrics["semantic_outlier_count"] = int(semantic_outlier_count or 0)
        for idx, issues in semantic_issues.items():
            if idx in all_diags:
                all_diags[idx].issues.extend(issues)

        repetition_issues = self._analyze_repetition_patterns(sentences_with_pos, primary_lang)
        for idx, issues in repetition_issues.items():
            if idx in all_diags:
                all_diags[idx].issues.extend(issues)

        syntax_issues, syntax_outlier_count, syntax_available = self._analyze_syntax_outliers(
            sentences_with_pos, primary_lang
        )
        domain_metrics["syntax_outlier_count"] = int(syntax_outlier_count or 0)
        domain_metrics["syntax_available"] = bool(syntax_available)
        for idx, issues in syntax_issues.items():
            if idx in all_diags:
                all_diags[idx].issues.extend(issues)

        all_diagnoses = [d for d in all_diags.values() if d.issues]

        def _issue_rank(issue: SentenceIssue) -> int:
            severity = (issue.severity or "").lower()
            sev_boost = 1000 if severity == "warning" else 0
            type_weight = {
                "semantic_outlier": 520,
                "uncommon_phrasing": 440,
                "syntax_outlier": 420,
                "ai_transition": 320,
                "template": 300,
                "redundancy": 280,
                "repetition": 260,
                "punctuation": 240,
                "long_sentence": 180,
                "short_sentence": 160,
                "ai_word": 120,
                "passive": 110,
            }.get(issue.issue_type, 100)
            return sev_boost + type_weight

        def _diag_rank(diag: SentenceDiagnosis) -> int:
            if not diag.issues:
                return 0
            return max(_issue_rank(i) for i in diag.issues)

        for diag in all_diagnoses:
            try:
                diag.issues.sort(key=_issue_rank, reverse=True)
            except Exception:
                pass

        def _diag_severity_tuple(diag: SentenceDiagnosis) -> Tuple[int, int]:
            warn_count = 0
            for iss in (diag.issues or []):
                if (iss.severity or "").lower() == "warning":
                    warn_count += 1
            max_sev = 1 if warn_count > 0 else 0
            return max_sev, warn_count

        # Sort by severity first, then by importance, then by original order.
        all_diagnoses.sort(
            key=lambda d: (
                -_diag_severity_tuple(d)[0],
                -_diag_severity_tuple(d)[1],
                -_diag_rank(d),
                d.index,
            )
        )

        effective_sentence_count = sum(
            1 for (sent, _s, _e) in sentences_with_pos
            if not self._is_heading_like(sent, primary_lang)
        )
        if effective_sentence_count <= 0:
            effective_sentence_count = len(sentences_with_pos) or 1

        # Build phrase stats for UI (sorted by rarity)
        self.last_phrase_stats = self._build_phrase_stats(phrase_counter, primary_lang)

        self.last_weirdness = self._compute_weirdness_report(
            primary_lang=primary_lang,
            stats_by_type=stats_by_type,
            total_words=total_words,
            domain_metrics=domain_metrics,
            total_sentences=effective_sentence_count,
            style_sentence_count=len(style_diags),
        )

        self._analysis_ran = True
        self._all_sentence_diagnoses = all_diagnoses
        self._apply_diagnosis_filter()

        # Sort by doc_percent ascending (rarest first)
        self.last_word_stats = sorted(
            word_stats_dict.values(),
            key=lambda x: x['doc_percent']
        )

        # Update stats table
        self.update_stats_table()
        self._update_stats_hint()
        self._update_weirdness_badge()

    def _primary_domain_language(self, text: str, detected_language: str) -> str:
        """Pick a primary language ('en'|'zh') for corpus-based diagnostics."""
        if detected_language in ('en', 'zh'):
            return detected_language
        # mixed: decide by char counts
        sample = text[:50000] if len(text) > 50000 else text
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', sample))
        english_chars = len(re.findall(r'[a-zA-Z]', sample))
        return 'zh' if chinese_chars >= english_chars else 'en'

    def _is_heading_like(self, sentence: str, language: str) -> bool:
        return is_heading_like(sentence, language)

    def _split_sentences_with_positions(self, text: str, language: str) -> List[Tuple[str, int, int]]:
        """Fallback splitter when style analyzer is unavailable."""
        return split_sentences_with_positions(text, language)

    def _sentence_length(self, sentence: str, language: str) -> int:
        if language == 'zh':
            return len(re.findall(r'[\u4e00-\u9fff]', sentence))
        return len(re.findall(r'\b[a-z]+\b', sentence.lower()))

    def _tokenize_sentence_for_bigrams(self, sentence: str, language: str) -> List[str]:
        if language == 'en':
            return re.findall(r'\b[a-z]+\b', sentence.lower())
        # zh
        if jieba:
            tokens = jieba.lcut(sentence, cut_all=False)
        else:
            tokens = re.findall(r'[\u4e00-\u9fff]', sentence)
        cleaned = []
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            if not re.fullmatch(r'[\u4e00-\u9fff]+', tok):
                continue
            cleaned.append(tok)
        return cleaned

    @staticmethod
    def _detect_unbalanced_brackets(s: str) -> Optional[str]:
        pairs = [
            ('(', ')'),
            ('（', '）'),
            ('[', ']'),
            ('{', '}'),
            ('《', '》'),
            ('“', '”'),
            ('「', '」'),
            ('『', '』'),
        ]
        for left, right in pairs:
            if s.count(left) != s.count(right):
                return f"{left}{right}"
        return None

    def _analyze_domain_sentences(
        self,
        sentences_with_pos: List[Tuple[str, int, int]],
        primary_lang: str,
        text: str,
    ) -> Tuple[dict, dict, Counter]:
        """Corpus-based weirdness checks: short sentences, uncommon phrasing (bigrams), punctuation."""
        issues_by_idx: Dict[int, List[SentenceIssue]] = {}
        metrics = {
            "primary_lang": primary_lang,
            "short_sentence_count": 0,
            "punct_sentence_count": 0,
            "uncommon_phrasing_sentence_count": 0,
            "bigram_total": 0,
            "bigram_unseen": 0,
        }

        phrase_counter: Counter = Counter()

        baseline = self.corpus.get_sentence_length_baseline(primary_lang)
        mean_len = float(baseline.get("mean", 0.0) or 0.0)
        baseline_count = int(baseline.get("count", 0) or 0)

        # Sentence shortness threshold
        if mean_len > 0 and baseline_count >= 20:
            short_floor = 10 if primary_lang == 'zh' else 5
            short_threshold = max(short_floor, int(mean_len * 0.45))
        else:
            short_threshold = 10 if primary_lang == 'zh' else 5

        phrase_available = self.corpus.has_phrase_stats(primary_lang) and (self.corpus.doc_count_by_lang.get(primary_lang, 0) > 0)

        for idx, (sent, _start, _end) in enumerate(sentences_with_pos):
            local_issues: List[SentenceIssue] = []
            is_heading = self._is_heading_like(sent, primary_lang)

            # Short sentence check (domain-based)
            if not is_heading:
                sent_len = self._sentence_length(sent, primary_lang)
                if sent_len > 0 and sent_len < short_threshold:
                    metrics["short_sentence_count"] += 1
                    local_issues.append(SentenceIssue(
                        issue_type="short_sentence",
                        description=t("domain.short_sentence", count=sent_len, suggested=short_threshold),
                        severity="info",
                    ))

            # Punctuation / formatting anomalies (skip headings/math blocks to reduce false positives)
            if not is_heading:
                unbalanced = self._detect_unbalanced_brackets(sent)
                if unbalanced:
                    metrics["punct_sentence_count"] += 1
                    local_issues.append(SentenceIssue(
                        issue_type="punctuation",
                        description=t("domain.unbalanced_brackets", pair=unbalanced),
                        severity="info",
                        matched_text=unbalanced
                    ))

                repeated = re.search(r'([!?！？。；;，,])\\1{1,}', sent)
                if repeated:
                    metrics["punct_sentence_count"] += 1
                    local_issues.append(SentenceIssue(
                        issue_type="punctuation",
                        description=t("domain.repeated_punct", punct=repeated.group(0)),
                        severity="info",
                        matched_text=repeated.group(0)
                    ))

            # Uncommon phrasing via corpus bigrams
            if phrase_available and not is_heading:
                toks = self._tokenize_sentence_for_bigrams(sent, primary_lang)
                if len(toks) >= 4:
                    total_bg = 0
                    unseen_bg = 0
                    examples = []
                    for a, b in zip(toks, toks[1:]):
                        key = f"{a}{NGRAM_SEP}{b}"
                        phrase_counter[key] += 1
                        total_bg += 1
                        metrics["bigram_total"] += 1
                        if self.corpus.bigram_doc_freq.get(primary_lang, Counter()).get(key, 0) == 0:
                            unseen_bg += 1
                            metrics["bigram_unseen"] += 1
                            if len(examples) < 3:
                                examples.append(key.replace(NGRAM_SEP, " "))
                    # Corpus sentences have 0 unseen bigrams; for user text, even a moderate unseen density can be meaningful.
                    if total_bg >= 8 and unseen_bg >= 3:
                        ratio = unseen_bg / total_bg if total_bg else 0.0
                        severity = None
                        if unseen_bg >= 4 and ratio >= 0.33:
                            severity = "warning"
                        elif unseen_bg >= 3 and ratio >= 0.25:
                            severity = "info"
                        if severity:
                            metrics["uncommon_phrasing_sentence_count"] += 1
                            example = " / ".join(examples) if examples else ""
                            local_issues.append(SentenceIssue(
                                issue_type="uncommon_phrasing",
                                description=t("domain.uncommon_phrasing", unseen=unseen_bg, total=total_bg, example=example),
                                severity=severity,
                                matched_text=example
                            ))

            if local_issues:
                issues_by_idx[idx] = local_issues

        return issues_by_idx, metrics, phrase_counter

    def _analyze_repetition_patterns(
        self,
        sentences_with_pos: List[Tuple[str, int, int]],
        primary_lang: str,
    ) -> Dict[int, List[SentenceIssue]]:
        """Detect repeated sentence openers (common in template/AI-like writing)."""
        try:
            lang = (primary_lang or "en").strip().lower()
        except Exception:
            lang = "en"
        if lang not in ("en", "zh"):
            lang = "en"

        starters: Dict[int, str] = {}
        counts: Counter = Counter()

        for idx, (sent, _s, _e) in enumerate(sentences_with_pos):
            if self._is_heading_like(sent, lang):
                continue
            clean = re.sub(r"\s+", " ", (sent or "").strip())
            if not clean:
                continue

            if lang == "en":
                toks = re.findall(r"\b[a-z]+\b", clean.lower())
                if len(toks) < 6:
                    continue
                key = " ".join(toks[:3])
            else:
                toks = self._tokenize_sentence_for_bigrams(clean, "zh")
                if len(toks) < 5:
                    continue
                key = "".join(toks[:3])

            if not key:
                continue
            starters[idx] = key
            counts[key] += 1

        repeated = {k: int(v) for k, v in counts.items() if int(v) >= 3}
        if not repeated:
            return {}

        issues_by_idx: Dict[int, List[SentenceIssue]] = {}
        for idx, key in starters.items():
            c = repeated.get(key, 0)
            if c >= 3:
                issues_by_idx[idx] = [SentenceIssue(
                    issue_type="repetition",
                    description=t("style.repetition", starter=key, count=c),
                    severity="info",
                    matched_text=key,
                )]

        return issues_by_idx

    def _analyze_syntax_outliers(
        self,
        sentences_with_pos: List[Tuple[str, int, int]],
        primary_lang: str,
    ) -> Tuple[Dict[int, List[SentenceIssue]], int, bool]:
        """Detect POS-pattern outliers vs the corpus (requires syntax stats + UDPipe)."""
        try:
            lang = (primary_lang or "en").strip().lower()
        except Exception:
            lang = "en"
        if lang not in ("en", "zh"):
            lang = "en"

        analyzer = getattr(self, "syntax_analyzer", None)
        has_stats = bool(getattr(self.corpus, "has_syntax_stats", lambda _l: False)(lang))
        if analyzer is None or not has_stats:
            return {}, 0, False

        total_sents = int(getattr(self.corpus, "pos_bigram_sentence_total", Counter()).get(lang, 0) or 0)
        if total_sents <= 0:
            return {}, 0, False

        freq = getattr(self.corpus, "pos_bigram_sentence_freq", {}).get(lang, Counter())
        issues_by_idx: Dict[int, List[SentenceIssue]] = {}
        outlier_count = 0

        for idx, (sent, _s, _e) in enumerate(sentences_with_pos):
            if self._is_heading_like(sent, lang):
                continue
            clean = re.sub(r"\s+", " ", (sent or "").strip())
            if not clean:
                continue
            # Skip very short fragments
            if lang == "en":
                if len(re.findall(r"\b[a-z]+\b", clean.lower())) < 8:
                    continue
            else:
                if len(re.findall(r"[\u4e00-\u9fff]", clean)) < 16:
                    continue

            try:
                parsed = analyzer.analyze_sentence(clean, lang)
            except Exception:
                parsed = None
            if not parsed or not isinstance(parsed, dict):
                continue

            upos = list(parsed.get("upos", []) or [])
            if len(upos) < 4:
                continue

            total_bg = 0
            unseen_bg = 0
            examples = []
            for a, b in zip(upos, upos[1:]):
                if not a or not b:
                    continue
                total_bg += 1
                key = f"{a}{NGRAM_SEP}{b}"
                if int(freq.get(key, 0) or 0) == 0:
                    unseen_bg += 1
                    if len(examples) < 2:
                        examples.append(key.replace(NGRAM_SEP, "→"))

            if total_bg >= 5 and unseen_bg >= 3 and (unseen_bg / max(1, total_bg)) >= 0.55:
                outlier_count += 1
                example = examples[0] if examples else ""
                issues_by_idx[idx] = [SentenceIssue(
                    issue_type="syntax_outlier",
                    description=t("syntax.uncommon_pos", unseen=unseen_bg, total=total_bg, example=example),
                    severity="warning",
                    matched_text=example,
                )]

        return issues_by_idx, outlier_count, True

    def _build_phrase_stats(self, phrase_counter: Counter, primary_lang: str) -> List[dict]:
        if not phrase_counter:
            return []
        if not self.corpus.has_phrase_stats(primary_lang):
            return []
        stats_list = []
        for key, count in phrase_counter.items():
            s = self.corpus.get_bigram_stats(key, language=primary_lang)
            s["count_in_text"] = int(count)
            stats_list.append(s)
        stats_list.sort(key=lambda x: (x.get("doc_percent", 0.0), -x.get("count_in_text", 0)))
        return stats_list[:300]

    def _get_semantic_index(self) -> Optional[SemanticSentenceIndex]:
        if not self.corpus.library_path:
            return None
        if self.semantic_index is not None and self.semantic_index_library_path == self.corpus.library_path:
            return self.semantic_index

        paths = self.corpus.get_semantic_index_paths()
        if not paths:
            return None
        if not os.path.exists(paths.get('sentences', '')) or not os.path.exists(paths.get('embeddings', '')):
            return None
        try:
            self.semantic_index = SemanticSentenceIndex.load(paths['sentences'], paths['embeddings'])
            self.semantic_index_library_path = self.corpus.library_path
            return self.semantic_index
        except Exception:
            self.semantic_index = None
            self.semantic_index_library_path = None
            return None

    def _analyze_semantic_similarity(
        self,
        sentences_with_pos: List[Tuple[str, int, int]],
        primary_lang: str,
        threshold: float = SEMANTIC_SIM_THRESHOLD,
        top_k: int = 3,
    ) -> Tuple[Dict[int, List[SentenceIssue]], int]:
        if self.semantic_embedder is None:
            return {}, 0

        index = self._get_semantic_index()
        if index is None or not index.sentences:
            return {}, 0

        query_sentences = []
        mapping = []
        for idx, (sent, _s, _e) in enumerate(sentences_with_pos):
            if self._is_heading_like(sent, primary_lang):
                continue
            clean = re.sub(r'\s+', ' ', sent).strip()
            if not clean:
                continue
            # Skip very short fragments
            if primary_lang == 'en':
                if len(re.findall(r'\b[a-z]+\b', clean.lower())) < 6:
                    continue
            else:
                if len(re.findall(r'[\u4e00-\u9fff]', clean)) < 12:
                    continue
            query_sentences.append(clean)
            mapping.append(idx)

        if not query_sentences:
            return {}, 0

        try:
            q_emb = self.semantic_embedder.embed(query_sentences, batch_size=32)
        except Exception:
            return {}, 0

        issues_by_idx: Dict[int, List[SentenceIssue]] = {}
        outlier_count = 0

        for i, vec in enumerate(q_emb):
            top = index.query_topk(vec, top_k=top_k)
            if not top:
                continue
            best_score, best_idx = top[0]
            if best_score < threshold:
                outlier_count += 1
                example = index.get_sentence(best_idx)
                example_preview = example.strip().replace("\n", " ")
                if len(example_preview) > 120:
                    example_preview = example_preview[:119] + "…"
                src = self._format_semantic_source(index.get_source(best_idx))
                if src:
                    desc = t("semantic.low_similarity_with_source", score=best_score, suggested=threshold, example=example_preview, source=src)
                else:
                    desc = t("semantic.low_similarity", score=best_score, suggested=threshold, example=example_preview)
                issues_by_idx[mapping[i]] = [SentenceIssue(
                    issue_type="semantic_outlier",
                    description=desc,
                    severity="warning",
                    matched_text=f"{best_score:.3f}"
                )]

        return issues_by_idx, outlier_count

    def _compute_weirdness_report(
        self,
        primary_lang: str,
        stats_by_type: dict,
        total_words: int,
        domain_metrics: dict,
        total_sentences: int,
        style_sentence_count: int,
    ) -> dict:
        total_words = max(1, int(total_words or 0))
        unseen_words = int(stats_by_type.get("unseen", 0) or 0)
        rare_words = int(stats_by_type.get("rare", 0) or 0)
        unseen_ratio = unseen_words / total_words
        rare_ratio = rare_words / total_words

        bigram_total = int(domain_metrics.get("bigram_total", 0) or 0)
        bigram_unseen = int(domain_metrics.get("bigram_unseen", 0) or 0)
        bigram_unseen_ratio = (bigram_unseen / bigram_total) if bigram_total > 0 else 0.0

        short_sents = int(domain_metrics.get("short_sentence_count", 0) or 0)
        punct_sents = int(domain_metrics.get("punct_sentence_count", 0) or 0)
        phrasing_sents = int(domain_metrics.get("uncommon_phrasing_sentence_count", 0) or 0)
        outlier_ratio = min(1.0, (short_sents + punct_sents + phrasing_sents) / max(1, total_sentences))
        style_ratio = min(1.0, style_sentence_count / max(1, total_sentences))

        semantic_outliers = int(domain_metrics.get("semantic_outlier_count", 0) or 0)
        semantic_ratio = min(1.0, semantic_outliers / max(1, total_sentences))

        syntax_available = bool(domain_metrics.get("syntax_available", False))
        syntax_outliers = int(domain_metrics.get("syntax_outlier_count", 0) or 0)
        syntax_ratio = min(1.0, syntax_outliers / max(1, total_sentences)) if syntax_available else 0.0

        # Dynamic weights: shift weight when phrase/syntax stats are unavailable.
        has_phrase = bigram_total > 0
        has_syntax = bool(syntax_available)
        if has_phrase and has_syntax:
            w_words, w_phrase, w_sent, w_style, w_sem, w_syn = 0.32, 0.26, 0.12, 0.10, 0.14, 0.06
        elif has_phrase and not has_syntax:
            w_words, w_phrase, w_sent, w_style, w_sem, w_syn = 0.34, 0.28, 0.13, 0.10, 0.15, 0.0
        elif (not has_phrase) and has_syntax:
            w_words, w_phrase, w_sent, w_style, w_sem, w_syn = 0.46, 0.0, 0.19, 0.15, 0.14, 0.06
        else:
            w_words, w_phrase, w_sent, w_style, w_sem, w_syn = 0.50, 0.0, 0.20, 0.15, 0.15, 0.0

        word_component = min(1.0, unseen_ratio * 1.25 + rare_ratio * 0.75)
        phrase_component = min(1.0, bigram_unseen_ratio * 1.6) if has_phrase else 0.0
        sent_component = min(1.0, outlier_ratio * 1.2)
        style_component = min(1.0, style_ratio * 1.0)
        semantic_component = min(1.0, semantic_ratio * 1.1)
        syntax_component = min(1.0, syntax_ratio * 1.1) if has_syntax else 0.0

        score = int(round(100 * (
            w_words * word_component
            + w_phrase * phrase_component
            + w_sent * sent_component
            + w_style * style_component
            + w_sem * semantic_component
            + w_syn * syntax_component
        )))
        score = max(0, min(100, score))

        if score >= 70:
            level = "high"
        elif score >= 35:
            level = "medium"
        else:
            level = "low"

        return {
            "primary_lang": primary_lang,
            "score": score,
            "level": level,
            "word_unseen_ratio": unseen_ratio,
            "word_rare_ratio": rare_ratio,
            "bigram_unseen_ratio": bigram_unseen_ratio,
            "outlier_ratio": outlier_ratio,
            "style_ratio": style_ratio,
            "semantic_ratio": semantic_ratio,
            "syntax_ratio": syntax_ratio,
            "syntax_available": has_syntax,
            "phrase_available": has_phrase,
            "corpus_lang": self.corpus.language,
            "corpus_docs_en": int(self.corpus.doc_count_by_lang.get("en", 0) or 0),
            "corpus_docs_zh": int(self.corpus.doc_count_by_lang.get("zh", 0) or 0),
        }

    def _build_sentence_issue_map(self):
        """Build a map of text positions to sentence issues for tooltip display"""
        self.sentence_issue_map = {}
        for diag in self.last_sentence_diagnoses:
            # Store issues for each character position in the sentence
            for pos in range(diag.start_pos, diag.end_pos):
                self.sentence_issue_map[pos] = diag

    def _highlight_problem_sentences(self):
        """Apply style_issue tag to sentences with detected issues"""
        for diag in self.last_sentence_diagnoses:
            # Convert character positions to Text widget indices
            start_idx = f"1.0+{diag.start_pos}c"
            end_idx = f"1.0+{diag.end_pos}c"
            self.result_text.tag_add("style_issue", start_idx, end_idx)

    def _get_tooltip_for_position(self, text_index: str) -> str:
        """Get tooltip text for a given text widget index"""
        try:
            # Convert text index to character position
            line, col = map(int, text_index.split('.'))
            # Count characters up to this position
            char_pos = 0
            for i in range(1, line):
                line_text = self.result_text.get(f"{i}.0", f"{i}.end")
                char_pos += len(line_text) + 1  # +1 for newline
            char_pos += col

            # Look up issues for this position
            if char_pos in self.sentence_issue_map:
                diag = self.sentence_issue_map[char_pos]
                issues_text = "\n".join(f"• {issue.description}" for issue in diag.issues)
                return issues_text
        except Exception:
            pass
        return ""

    def update_diagnosis_panel(self):
        """Update the sentence diagnosis panel with detected issues"""
        widget = getattr(self, "diag_text", None)
        if widget is None or not getattr(widget, "winfo_exists", lambda: 0)():
            return

        try:
            widget.config(state=tk.NORMAL)
        except Exception:
            pass
        try:
            widget.delete("1.0", tk.END)
        except Exception:
            return

        try:
            self._diag_tag_map = {}
        except Exception:
            self._diag_tag_map = {}

        if not self.last_sentence_diagnoses:
            analysis_ran = bool(getattr(self, "_analysis_ran", False))
            all_total = len(getattr(self, "_all_sentence_diagnoses", []) or [])
            placeholder_key = "style.placeholder"
            if analysis_ran and all_total <= 0:
                placeholder_key = "style.placeholder_no_issues"
            elif all_total > 0 and not bool(getattr(self, "_show_minor_issues", True)):
                placeholder_key = "style.placeholder_no_severe"

            placeholder_fg = Theme.SUCCESS if placeholder_key == "style.placeholder_no_issues" else Theme.TEXT_MUTED
            diag_font = max(11, int(getattr(self, "font_size", 13) or 13))
            try:
                widget.config(font=(FONT_UI, diag_font))
                widget.tag_configure("diag_placeholder", font=(FONT_UI, diag_font), foreground=placeholder_fg)
                widget.insert("1.0", t(placeholder_key), ("diag_placeholder",))
            except Exception:
                pass
            return

        diag_font = max(11, int(getattr(self, "font_size", 13) or 13))
        diag_font_small = max(10, diag_font - 1)
        diag_font_bold = diag_font

        try:
            widget.config(font=(FONT_UI, diag_font))
        except Exception:
            pass
        try:
            widget.tag_configure("diag_header_warning", font=(FONT_UI, diag_font_bold, "bold"), foreground=Theme.WARNING)
            widget.tag_configure("diag_header_info", font=(FONT_UI, diag_font_bold, "bold"), foreground=Theme.PRIMARY)
            widget.tag_configure("diag_sentence", font=(FONT_UI, diag_font), foreground=Theme.TEXT_SECONDARY)
            widget.tag_configure("diag_issues", font=(FONT_UI, diag_font_small), foreground=Theme.TEXT_MUTED)
            widget.tag_configure("diag_detail", font=(FONT_UI, diag_font), foreground=Theme.TEXT_PRIMARY)
            widget.tag_configure("diag_pdf", font=(FONT_UI, diag_font), foreground=Theme.PRIMARY, underline=True)
        except Exception:
            pass

        pdf_re = re.compile(r"PDF[:：]\s*[^)）\n]+")

        for i, diag in enumerate(self.last_sentence_diagnoses):
            tag_name = f"diag_item_{i}"
            try:
                self._diag_tag_map[tag_name] = diag
            except Exception:
                pass

            max_sev = "warning" if any((iss.severity or "").lower() == "warning" for iss in (diag.issues or [])) else "info"
            icon = "⚠" if max_sev == "warning" else "ℹ"
            header_tag = "diag_header_warning" if max_sev == "warning" else "diag_header_info"

            header_line = f"{icon} {t('style.sentence')} {diag.index + 1}:\n"
            try:
                widget.insert(tk.END, header_line, (tag_name, header_tag))
            except Exception:
                pass

            sentence_text = (diag.text or "").strip().replace("\n", " ")
            try:
                widget.insert(tk.END, f"{sentence_text}\n", (tag_name, "diag_sentence"))
            except Exception:
                pass

            issue_types = []
            issue_details = []
            for issue in (diag.issues or []):
                type_label = self._get_issue_type_label(issue.issue_type)
                if issue.issue_type == "semantic_outlier" and issue.matched_text:
                    issue_types.append(f"{type_label} ({issue.matched_text})")
                elif issue.matched_text:
                    issue_types.append(f"{type_label}: \"{issue.matched_text}\"")
                else:
                    issue_types.append(type_label)
                if issue.description:
                    issue_details.append(issue.description)

            issues_text = " | ".join(issue_types)
            try:
                widget.insert(tk.END, f"└─ {issues_text}\n", (tag_name, "diag_issues"))
            except Exception:
                pass

            for desc in issue_details:
                line = f"• {desc}\n"
                try:
                    start_idx = widget.index("end-1c")
                    widget.insert(tk.END, line, (tag_name, "diag_detail"))
                    for m in pdf_re.finditer(line):
                        try:
                            widget.tag_add("diag_pdf", f"{start_idx}+{m.start()}c", f"{start_idx}+{m.end()}c")
                        except Exception:
                            pass
                except Exception:
                    pass

            try:
                widget.insert(tk.END, "\n", (tag_name,))
            except Exception:
                pass

    def _get_issue_type_label(self, issue_type: str) -> str:
        """Get display label for issue type"""
        type_labels = {
            'long_sentence': t("style.type.long"),
            'short_sentence': t("style.type.short"),
            'ai_transition': t("style.type.transition"),
            'ai_word': t("style.type.ai_word"),
            'passive': t("style.type.passive"),
            'template': t("style.type.template"),
            'uncommon_phrasing': t("style.type.uncommon_phrasing"),
            'punctuation': t("style.type.punctuation"),
            'semantic_outlier': t("style.type.semantic"),
            'redundancy': t("style.type.redundancy"),
            'repetition': t("style.type.repetition"),
            'syntax_outlier': t("style.type.syntax"),
        }
        return type_labels.get(issue_type, issue_type)


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = ModernApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
