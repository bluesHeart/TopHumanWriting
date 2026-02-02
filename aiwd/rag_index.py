# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
    from llama_index.core.base.embeddings.base import BaseEmbedding
    from llama_index.core.schema import TextNode
    from llama_index.core.utils import set_global_tokenizer
except Exception:  # pragma: no cover
    StorageContext = None
    VectorStoreIndex = None
    load_index_from_storage = None
    BaseEmbedding = object
    TextNode = None
    set_global_tokenizer = None

try:
    from llama_index.vector_stores.faiss import FaissVectorStore
except Exception:  # pragma: no cover
    FaissVectorStore = None

try:
    from llama_index.vector_stores.chroma import ChromaVectorStore
except Exception:  # pragma: no cover
    ChromaVectorStore = None


_TOKEN_RE = re.compile(r"[A-Za-z]+|\d+(?:\.\d+)?|[\u4e00-\u9fff]|\S", flags=re.UNICODE)


def _ensure_llamaindex_tokenizer():
    """
    LlamaIndex defaults to tiktoken (cl100k_base). In some bundled builds,
    tiktoken entrypoints can be missing, causing `Unknown encoding cl100k_base`.
    We do not rely on exact token counts for our pipeline (we chunk by chars),
    so we set a lightweight fallback tokenizer to avoid hard dependency on tiktoken.
    """
    if set_global_tokenizer is None:
        return
    try:
        import llama_index.core  # type: ignore
    except Exception:
        return
    try:
        if getattr(llama_index.core, "global_tokenizer", None) is not None:
            return
    except Exception:
        pass

    def _tok(text: str) -> List[str]:
        return _TOKEN_RE.findall(text or "")

    try:
        set_global_tokenizer(_tok)
    except Exception:
        pass


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def is_page_number_line(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    if re.fullmatch(r"\d{1,4}", s):
        return True
    if re.fullmatch(r"page\s*\d{1,4}", s.lower()):
        return True
    return False


def strip_repeated_headers_footers(pages: List[List[str]]) -> List[List[str]]:
    if not pages:
        return pages
    n_pages = len(pages)
    if n_pages < 4:
        return pages

    head_counts: Dict[str, int] = {}
    foot_counts: Dict[str, int] = {}

    def bump(counter: Dict[str, int], line: str):
        line = normalize_ws(line)
        if not line:
            return
        if len(line) > 100:
            return
        counter[line] = counter.get(line, 0) + 1

    for lines in pages:
        if not lines:
            continue
        for line in lines[:2]:
            bump(head_counts, line)
        for line in lines[-2:]:
            bump(foot_counts, line)

    head_bad = {k for k, v in head_counts.items() if v >= int(0.6 * n_pages)}
    foot_bad = {k for k, v in foot_counts.items() if v >= int(0.6 * n_pages)}

    cleaned: List[List[str]] = []
    for lines in pages:
        new_lines: List[str] = []
        for i, line in enumerate(lines):
            ln = normalize_ws(line)
            if not ln:
                continue
            if is_page_number_line(ln):
                continue
            if i < 3 and ln in head_bad:
                continue
            if i >= max(0, len(lines) - 3) and ln in foot_bad:
                continue
            new_lines.append(line)
        cleaned.append(new_lines)
    return cleaned


def drop_references_tail(text: str) -> str:
    s = text or ""
    markers = [
        r"(?:^|\n)\s*references\s*\n",
        r"(?:^|\n)\s*bibliography\s*\n",
        r"(?:^|\n)\s*参考文献\s*\n",
        r"(?:^|\n)\s*引用文献\s*\n",
    ]
    for pat in markers:
        m = re.search(pat, s, flags=re.IGNORECASE)
        if m:
            head = s[: m.start()]
            # If the page is (mostly) the References section, drop it entirely.
            if len(normalize_ws(head)) < 40:
                return ""
            # Otherwise keep content before References heading.
            return head
    return s


def is_reference_like_text(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    head = s[:80].strip()
    head_l = head.lower()
    if head_l.startswith("references") or head_l.startswith("bibliography"):
        return True
    if head.startswith("参考文献") or head.startswith("引用文献"):
        return True
    return False


def split_paragraphs(text: str) -> List[str]:
    # Keep it simple and robust for PDF artifacts.
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n\s*\n+", raw)
    out: List[str] = []
    for p in parts:
        p = normalize_ws(p)
        if not p:
            continue
        out.append(p)
    return out


@dataclass(frozen=True)
class RagNode:
    id: str
    text: str
    pdf: str
    page: int


@dataclass
class RagBuildStats:
    pdf_count: int = 0
    page_count: int = 0
    node_count: int = 0
    embed_count: int = 0
    dim: int = 0


class RagIndexError(RuntimeError):
    pass


def normalize_rag_backend(name: str) -> str:
    """
    Normalize RAG vector-store backend name.

    Supported:
      - faiss  (default)
      - chroma
    """
    s = str(name or "").strip().lower()
    if not s or s == "auto":
        return "auto"
    if s in ("faiss", "faiss-cpu"):
        return "faiss"
    if s in ("chroma", "chromadb"):
        return "chroma"
    return s


def _rag_storage_subdir(backend: str) -> str:
    b = normalize_rag_backend(backend)
    if b in ("", "auto", "faiss"):
        return "storage"
    return f"storage_{b}"


def _chroma_collection_name(library_name: str) -> str:
    # Keep it stable and filesystem-safe.
    n = re.sub(r"[^A-Za-z0-9_\\-]+", "_", str(library_name or "").strip())
    n = re.sub(r"_+", "_", n).strip("_")
    if not n:
        n = "default"
    return f"tophumanwriting__{n}"


class RagSearchSession:
    """Keep a loaded LlamaIndex index in memory for repeated queries."""

    def __init__(
        self,
        *,
        storage_dir: str,
        embed_query: Callable[[str], "object"],
        backend: str = "faiss",
        chroma_path: str = "",
        chroma_collection: str = "",
    ):
        self.storage_dir = storage_dir
        self._embed_query = embed_query
        self._idx = None

        if StorageContext is None or load_index_from_storage is None:
            raise RagIndexError("缺少依赖：llama-index-core（用于范文证据检索）")
        if not os.path.exists(os.path.join(storage_dir, "docstore.json")):
            raise RagIndexError("范文证据索引缺失：请重新“准备范文库”")

        _ensure_llamaindex_tokenizer()
        embed_model = _CallableEmbedding(embed_sentences=None, embed_query=embed_query)
        b = normalize_rag_backend(backend)
        if b in ("", "auto"):
            b = "faiss"

        if b == "faiss":
            if FaissVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-faiss / faiss-cpu（建议：pip install tophumanwriting[rag-faiss]）"
                )
            vs = FaissVectorStore.from_persist_dir(storage_dir)
            sc = StorageContext.from_defaults(persist_dir=storage_dir, vector_store=vs)
            self._idx = load_index_from_storage(sc, embed_model=embed_model)
            return

        if b == "chroma":
            if ChromaVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-chroma / chromadb（建议：pip install tophumanwriting[rag-chroma]）"
                )
            try:
                import chromadb  # type: ignore
            except Exception as e:
                raise RagIndexError(f"缺少依赖：chromadb（{e}）")

            chroma_path = os.path.abspath(str(chroma_path or "").strip())
            if not chroma_path or not os.path.exists(chroma_path):
                raise RagIndexError("Chroma 持久化目录缺失：请重新“准备范文库”或指定正确的 chroma_path")
            collection_name = str(chroma_collection or "").strip() or _chroma_collection_name("default")
            client = chromadb.PersistentClient(path=chroma_path)
            collection = client.get_or_create_collection(collection_name)
            vs = ChromaVectorStore(chroma_collection=collection)
            sc = StorageContext.from_defaults(persist_dir=storage_dir, vector_store=vs)
            self._idx = load_index_from_storage(sc, embed_model=embed_model)
            return

        raise RagIndexError(f"Unknown RAG backend: {backend}")

    def search(self, query: str, *, top_k: int = 8) -> List[Tuple[float, RagNode]]:
        query = normalize_ws(query)
        if not query:
            return []
        idx = self._idx
        if idx is None:
            return []
        try:
            retriever = idx.as_retriever(similarity_top_k=max(1, min(int(top_k or 0), 50)))
            results = retriever.retrieve(query)
        except Exception:
            return []

        out: List[Tuple[float, RagNode]] = []
        for r in results:
            try:
                score = float(getattr(r, "score", 0.0) or 0.0)
                node = getattr(r, "node", None)
                if node is None:
                    continue
                meta = getattr(node, "metadata", {}) if hasattr(node, "metadata") else {}
                pdf = (meta.get("pdf", "") if isinstance(meta, dict) else "") or ""
                try:
                    page = int((meta.get("page", 0) if isinstance(meta, dict) else 0) or 0)
                except Exception:
                    page = 0
                text = ""
                try:
                    text = str(getattr(node, "text", "") or "")
                except Exception:
                    try:
                        text = str(getattr(node, "get_text", lambda: "")() or "")
                    except Exception:
                        text = ""
                if is_reference_like_text(text):
                    continue
                nid = str(getattr(node, "id_", "") or "")
                out.append((score, RagNode(id=nid, text=text, pdf=pdf, page=page)))
            except Exception:
                continue
        return out


class RagIndexer:
    """
    Build a lightweight, citation-friendly RAG corpus from a local PDF folder.

    Storage (per library):
      - manifest.json
      - nodes.jsonl
      - storage/           (faiss, legacy default)
      - storage_chroma/    (chroma)
      - chroma/            (chroma persistent store)
    """

    def __init__(self, *, data_dir: str, library_name: str, backend: str = "auto"):
        self.data_dir = data_dir
        self.library_name = library_name
        self.base_dir = os.path.join(data_dir, "rag", library_name)
        self.manifest_path = os.path.join(self.base_dir, "manifest.json")
        self.nodes_path = os.path.join(self.base_dir, "nodes.jsonl")
        # Backend resolution:
        #   1) explicit argument (if not auto)
        #   2) manifest.json backend (new format)
        #   3) detect existing index folders (old format had no backend field)
        #   4) env TOPHUMANWRITING_RAG_BACKEND
        #   5) auto by installed extras (faiss if available else chroma)
        want = normalize_rag_backend(backend)
        if want in ("", "auto"):
            try:
                m = self._load_manifest()
            except Exception:
                m = {}
            m_backend = normalize_rag_backend(str((m or {}).get("backend", "") or ""))
            if m_backend not in ("", "auto"):
                want = m_backend
            else:
                detected = ""
                try:
                    faiss_storage = os.path.join(self.base_dir, _rag_storage_subdir("faiss"))
                    chroma_storage = os.path.join(self.base_dir, _rag_storage_subdir("chroma"))
                    faiss_ready = os.path.exists(os.path.join(faiss_storage, "docstore.json"))
                    chroma_ready = os.path.exists(os.path.join(chroma_storage, "docstore.json")) or os.path.exists(
                        os.path.join(self.base_dir, "chroma")
                    )
                    if faiss_ready and not chroma_ready:
                        detected = "faiss"
                    elif chroma_ready and not faiss_ready:
                        detected = "chroma"
                except Exception:
                    detected = ""

                if detected:
                    want = detected
                else:
                    env_backend = normalize_rag_backend(os.environ.get("TOPHUMANWRITING_RAG_BACKEND", "") or "")
                    if env_backend not in ("", "auto"):
                        want = env_backend
                    else:
                        # Best-effort auto selection based on installed extras.
                        if FaissVectorStore is None and ChromaVectorStore is not None:
                            want = "chroma"
                        else:
                            want = "faiss"
        self.backend = want

        # LlamaIndex persisted storage lives here (docstore/index_store).
        self.storage_dir = os.path.join(self.base_dir, _rag_storage_subdir(self.backend))
        self.chroma_dir = os.path.join(self.base_dir, "chroma")
        self.chroma_collection = _chroma_collection_name(library_name)
        # Legacy / debug: raw FAISS index path (optional; not used when LlamaIndex storage exists).
        self.faiss_path = os.path.join(self.base_dir, "faiss.index")

    def _ensure_dir(self):
        os.makedirs(self.base_dir, exist_ok=True)
        os.makedirs(self.storage_dir, exist_ok=True)
        if self.backend == "chroma":
            os.makedirs(self.chroma_dir, exist_ok=True)

    @staticmethod
    def _rel_pdf_path(pdf_path: str, root: str) -> str:
        try:
            return os.path.relpath(pdf_path, root).replace("\\", "/")
        except Exception:
            return os.path.basename(pdf_path).replace("\\", "/")

    @staticmethod
    def _iter_pdfs(folder: str) -> List[str]:
        p = Path(folder)
        return [str(x) for x in p.rglob("*.pdf")]

    @staticmethod
    def _file_sig(path: str) -> dict:
        try:
            st = os.stat(path)
            return {"size": int(st.st_size), "mtime": int(st.st_mtime)}
        except Exception:
            return {"size": 0, "mtime": 0}

    def _load_manifest(self) -> dict:
        try:
            if os.path.exists(self.manifest_path):
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
        return {}

    def _write_manifest(self, manifest: dict):
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.manifest_path)

    def _write_nodes(self, nodes: Sequence[RagNode]):
        tmp = self.nodes_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for n in nodes:
                f.write(json.dumps({"id": n.id, "text": n.text, "pdf": n.pdf, "page": n.page}, ensure_ascii=False))
                f.write("\n")
        os.replace(tmp, self.nodes_path)

    @staticmethod
    def _node_id(pdf_rel: str, page_0: int, idx_in_page: int) -> str:
        return f"{pdf_rel}#p{page_0+1}#{idx_in_page}"

    def build(
        self,
        pdf_root: str,
        *,
        embed_sentences: Callable[[List[str], Optional[Callable[[int, int], None]], Optional[Callable[[], bool]]], "object"],
        embed_query: Callable[[str], "object"],
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        max_nodes: int = 120000,
        min_chars: int = 60,
        max_chars: int = 900,
    ) -> RagBuildStats:
        if fitz is None:
            raise RagIndexError("缺少依赖：PyMuPDF（fitz，用于解析 PDF）")
        _ensure_llamaindex_tokenizer()

        self._ensure_dir()
        stats = RagBuildStats()

        pdfs = self._iter_pdfs(pdf_root)
        stats.pdf_count = len(pdfs)

        # Build nodes (full rebuild for now; manifest is still written for future incremental improvements).
        nodes: List[RagNode] = []

        def report(stage: str, done: int, total: int, detail: str):
            if progress_cb:
                try:
                    progress_cb(stage, int(done), int(total), detail or "")
                except Exception:
                    pass

        report("rag_extract", 0, len(pdfs), "")

        for i, pdf_path in enumerate(pdfs):
            if cancel_cb and cancel_cb():
                raise RagIndexError("canceled")

            rel = self._rel_pdf_path(pdf_path, pdf_root)
            report("rag_extract", i + 1, len(pdfs), rel)

            try:
                doc = fitz.open(pdf_path)
            except Exception:
                continue

            try:
                pages_lines: List[List[str]] = []
                for page in doc:
                    if cancel_cb and cancel_cb():
                        raise RagIndexError("canceled")
                    try:
                        txt = page.get_text(flags=fitz.TEXT_DEHYPHENATE)
                    except Exception:
                        txt = ""
                    txt = drop_references_tail(txt)
                    lines = [ln for ln in (txt or "").splitlines() if normalize_ws(ln)]
                    pages_lines.append(lines)
                pages_lines = strip_repeated_headers_footers(pages_lines)

                for page_0, lines in enumerate(pages_lines):
                    stats.page_count += 1
                    page_text = "\n".join(lines)
                    page_text = drop_references_tail(page_text)
                    paras = split_paragraphs(page_text)
                    # Some PDFs produce extra blank lines that split a paragraph into many tiny fragments.
                    # If all fragments are shorter than min_chars but the joined text is usable, merge them.
                    try:
                        if paras and not any(len(p) >= int(min_chars) for p in paras):
                            joined = normalize_ws(" ".join(paras))
                            if len(joined) >= int(min_chars):
                                paras = [joined]
                    except Exception:
                        pass
                    idx_in_page = 0
                    for p in paras:
                        p = normalize_ws(p)
                        if len(p) < min_chars:
                            continue
                        if len(p) > max_chars:
                            # Hard split overly long paragraphs to avoid prompt/context blow-ups.
                            chunks = [p[j : j + max_chars] for j in range(0, len(p), max_chars)]
                        else:
                            chunks = [p]
                        for ch in chunks:
                            ch = normalize_ws(ch)
                            if len(ch) < min_chars:
                                continue
                            node_id = self._node_id(rel, page_0, idx_in_page)
                            idx_in_page += 1
                            nodes.append(RagNode(id=node_id, text=ch, pdf=rel, page=page_0 + 1))
                            if len(nodes) >= int(max_nodes):
                                break
                        if len(nodes) >= int(max_nodes):
                            break
                    if len(nodes) >= int(max_nodes):
                        break
            finally:
                try:
                    doc.close()
                except Exception:
                    pass

            if len(nodes) >= int(max_nodes):
                break

        stats.node_count = len(nodes)
        if stats.node_count <= 0:
            raise RagIndexError(
                "未从 PDF 提取到可用的范文片段（0 段）。可能原因：PDF 是扫描/图片版无可复制文本，或提取结果被切得过碎。"
                "建议：换可复制文本的 PDF 或先 OCR，再重新“一键准备”。"
            )

        report("rag_embed", 0, len(nodes), "")
        texts = [n.text for n in nodes]

        def embed_progress(done: int, total: int):
            report("rag_embed", done, total, "")

        embeddings = embed_sentences(texts, embed_progress, cancel_cb)
        if embeddings is None:
            raise RagIndexError("embedding failed")

        if StorageContext is None or VectorStoreIndex is None or TextNode is None:
            raise RagIndexError("缺少依赖：llama-index-core（用于构建范文证据）")
        if self.backend == "faiss":
            if FaissVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-faiss / faiss-cpu（建议：pip install tophumanwriting[rag-faiss]）"
                )
        elif self.backend == "chroma":
            if ChromaVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-chroma / chromadb（建议：pip install tophumanwriting[rag-chroma]）"
                )
        else:
            raise RagIndexError(f"Unknown RAG backend: {self.backend}")

        try:
            import numpy as np  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RagIndexError(f"缺少依赖：numpy（{e}）")

        vecs = embeddings
        if hasattr(vecs, "astype"):
            vecs = vecs.astype("float32", copy=False)
        vecs = np.asarray(vecs, dtype="float32")
        if vecs.ndim != 2 or vecs.shape[0] != len(nodes):
            raise RagIndexError("invalid embeddings shape")

        stats.embed_count = int(vecs.shape[0])
        stats.dim = int(vecs.shape[1])

        self._write_nodes(nodes)
        self._persist_llamaindex(nodes, vecs, embed_query=embed_query)

        manifest = {
            "version": 1,
            "built_at": int(time.time()),
            "backend": str(self.backend or "faiss"),
            "storage_subdir": _rag_storage_subdir(self.backend),
            "chroma_dir": "chroma" if self.backend == "chroma" else "",
            "chroma_collection": self.chroma_collection if self.backend == "chroma" else "",
            "pdf_root": os.path.abspath(pdf_root),
            "pdf_count": stats.pdf_count,
            "node_count": stats.node_count,
            "dim": stats.dim,
            "files": {self._rel_pdf_path(p, pdf_root): self._file_sig(p) for p in pdfs},
        }
        self._write_manifest(manifest)

        report("rag_done", len(nodes), len(nodes), "")
        return stats

    def _persist_llamaindex(self, nodes: Sequence[RagNode], vecs, *, embed_query: Callable[[str], "object"]):
        if StorageContext is None or VectorStoreIndex is None or TextNode is None:
            raise RagIndexError("缺少依赖：llama-index-core（用于构建范文证据）")

        embed_model = _CallableEmbedding(embed_sentences=None, embed_query=embed_query)

        # Prepare TextNodes with precomputed embeddings (no extra embedding inside LlamaIndex).
        text_nodes: List["TextNode"] = []
        for n, row in zip(nodes, vecs):
            tn = TextNode(
                id_=n.id,
                text=n.text,
                metadata={"pdf": n.pdf, "page": int(n.page or 0)},
            )
            tn.embedding = [float(x) for x in row.tolist()]
            text_nodes.append(tn)

        backend = normalize_rag_backend(self.backend)
        if backend in ("", "auto"):
            backend = "faiss"

        if backend == "faiss":
            if FaissVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-faiss / faiss-cpu（建议：pip install tophumanwriting[rag-faiss]）"
                )
            # Lazy import faiss here to keep module import light.
            try:
                import faiss  # type: ignore
            except Exception as e:  # pragma: no cover
                raise RagIndexError(f"缺少依赖：faiss-cpu（{e}）")

            vs = FaissVectorStore(faiss.IndexFlatIP(int(vecs.shape[1])))
            sc = StorageContext.from_defaults(vector_store=vs)
            _ = VectorStoreIndex(nodes=text_nodes, storage_context=sc, embed_model=embed_model)
            self._persist_storage_context(sc)
            return

        if backend == "chroma":
            if ChromaVectorStore is None:
                raise RagIndexError(
                    "缺少依赖：llama-index-vector-stores-chroma / chromadb（建议：pip install tophumanwriting[rag-chroma]）"
                )
            try:
                import chromadb  # type: ignore
            except Exception as e:
                raise RagIndexError(f"缺少依赖：chromadb（{e}）")

            os.makedirs(self.chroma_dir, exist_ok=True)
            client = chromadb.PersistentClient(path=self.chroma_dir)
            # Full rebuild for now: delete old collection to avoid duplicates.
            try:
                client.delete_collection(self.chroma_collection)
            except Exception:
                pass
            collection = client.get_or_create_collection(self.chroma_collection)
            vs = ChromaVectorStore(chroma_collection=collection)
            sc = StorageContext.from_defaults(vector_store=vs)
            _ = VectorStoreIndex(nodes=text_nodes, storage_context=sc, embed_model=embed_model)
            self._persist_storage_context(sc)
            return

        raise RagIndexError(f"Unknown RAG backend: {backend}")

    def _persist_storage_context(self, sc) -> None:
        # Persist to a temp dir, then atomically replace files to keep old index intact on failures.
        tmp_dir = self.storage_dir + ".tmp"
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except Exception:
            tmp_dir = self.storage_dir

        sc.persist(persist_dir=tmp_dir)

        if os.path.abspath(tmp_dir) != os.path.abspath(self.storage_dir):
            try:
                os.makedirs(self.storage_dir, exist_ok=True)
            except Exception:
                pass
            try:
                for name in os.listdir(tmp_dir):
                    src = os.path.join(tmp_dir, name)
                    dst = os.path.join(self.storage_dir, name)
                    if os.path.isfile(src):
                        os.replace(src, dst)
            except Exception:
                pass
            try:
                # Best-effort cleanup.
                for name in os.listdir(tmp_dir):
                    p = os.path.join(tmp_dir, name)
                    if os.path.isfile(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                os.rmdir(tmp_dir)
            except Exception:
                pass

    def load_nodes(self) -> List[RagNode]:
        nodes: List[RagNode] = []
        try:
            with open(self.nodes_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    nodes.append(RagNode(id=d.get("id", ""), text=d.get("text", ""), pdf=d.get("pdf", ""), page=int(d.get("page", 0) or 0)))
        except Exception:
            return []
        return nodes

    def search(
        self,
        query: str,
        *,
        embed_query: Callable[[str], "object"],
        top_k: int = 8,
    ) -> List[Tuple[float, RagNode]]:
        query = normalize_ws(query)
        if not query:
            return []
        try:
            sess = RagSearchSession(
                storage_dir=self.storage_dir,
                embed_query=embed_query,
                backend=self.backend,
                chroma_path=self.chroma_dir,
                chroma_collection=self.chroma_collection,
            )
        except Exception:
            return []
        return sess.search(query, top_k=top_k)

    def create_session(self, *, embed_query: Callable[[str], "object"]) -> RagSearchSession:
        return RagSearchSession(
            storage_dir=self.storage_dir,
            embed_query=embed_query,
            backend=self.backend,
            chroma_path=self.chroma_dir,
            chroma_collection=self.chroma_collection,
        )


class _CallableEmbedding(BaseEmbedding):
    def __init__(
        self,
        *,
        embed_sentences: Optional[Callable[[List[str], Optional[Callable[[int, int], None]], Optional[Callable[[], bool]]], "object"]],
        embed_query: Callable[[str], "object"],
    ):
        super().__init__()
        self._embed_sentences = embed_sentences
        self._embed_query = embed_query

    def _vec_to_list(self, v) -> List[float]:
        try:
            if hasattr(v, "tolist"):
                return [float(x) for x in v.tolist()]
        except Exception:
            pass
        try:
            return [float(x) for x in list(v)]
        except Exception:
            return []

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._vec_to_list(self._embed_query(query))

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return self._get_query_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        # Used only if nodes lack embeddings; in our pipeline nodes embed ahead of time.
        return self._get_query_embedding(text)
