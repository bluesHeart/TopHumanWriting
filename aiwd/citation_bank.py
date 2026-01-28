# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from aiwd.citeextract.pipeline import iter_citation_sentences_from_pages, load_pdf_pages
from aiwd.citeextract.references import iter_reference_entries_from_pages

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None


class CitationBankError(RuntimeError):
    pass


@dataclass
class CitationBankBuildStats:
    pdf_count: int = 0
    citation_sentence_count: int = 0
    reference_count: int = 0
    dim: int = 0
    seconds: float = 0.0


@dataclass(frozen=True)
class CitationHit:
    score: float
    pdf: str
    page: int
    sentence: str
    citations: List[Dict[str, str]]


class CitationBankIndexer:
    """
    Build and search a citation sentence bank from a local PDF folder.

    Storage (per library):
      - manifest.json
      - citations.jsonl
      - references.jsonl
      - citations.embeddings.npy
      - citations.semantic_meta.json
      - citations.faiss.index
    """

    def __init__(self, *, data_dir: str, library_name: str):
        self.data_dir = data_dir
        self.library_name = library_name
        self.base_dir = os.path.join(data_dir, "cite", library_name)
        self.manifest_path = os.path.join(self.base_dir, "manifest.json")
        self.citations_path = os.path.join(self.base_dir, "citations.jsonl")
        self.references_path = os.path.join(self.base_dir, "references.jsonl")
        self.embeddings_path = os.path.join(self.base_dir, "citations.embeddings.npy")
        self.meta_path = os.path.join(self.base_dir, "citations.semantic_meta.json")
        self.faiss_path = os.path.join(self.base_dir, "citations.faiss.index")

    def _ensure_dir(self) -> None:
        os.makedirs(self.base_dir, exist_ok=True)

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

    def index_ready(self) -> bool:
        try:
            return (
                os.path.exists(self.citations_path)
                and os.path.exists(self.references_path)
                and os.path.exists(self.embeddings_path)
                and os.path.exists(self.meta_path)
                and os.path.exists(self.faiss_path)
            )
        except Exception:
            return False

    def load_manifest(self) -> dict:
        try:
            if os.path.exists(self.manifest_path):
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                return d if isinstance(d, dict) else {}
        except Exception:
            pass
        return {}

    def load_citations(self) -> List[dict]:
        out: List[dict] = []
        try:
            with open(self.citations_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
        except Exception:
            return []
        return out

    def load_references(self) -> List[dict]:
        out: List[dict] = []
        try:
            with open(self.references_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
        except Exception:
            return []
        return out

    def build(
        self,
        *,
        pdf_root: str,
        embed_sentences: Callable[
            [Sequence[str], Optional[Callable[[int, int], None]], Optional[Callable[[], bool]]],
            "object",
        ],
        progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
        max_pages: Optional[int] = None,
        stop_at_references: bool = True,
        max_citation_sentences: int = 80000,
    ) -> CitationBankBuildStats:
        if np is None:
            raise CitationBankError("numpy is required")

        self._ensure_dir()
        stats = CitationBankBuildStats()
        t0 = time.time()

        pdfs = self._iter_pdfs(pdf_root)
        stats.pdf_count = len(pdfs)

        citations_tmp = self.citations_path + ".tmp"
        refs_tmp = self.references_path + ".tmp"

        def report(stage: str, done: int, total: int, detail: str = "") -> None:
            if progress_cb:
                try:
                    progress_cb(stage, int(done), int(total), detail or "")
                except Exception:
                    pass

        # 1) Extract citations + references (JSONL)
        report("cite_extract", 0, len(pdfs), "")
        with open(citations_tmp, "w", encoding="utf-8") as f_c, open(refs_tmp, "w", encoding="utf-8") as f_r:
            for i, pdf_path in enumerate(pdfs):
                if cancel_cb and cancel_cb():
                    raise CitationBankError("canceled")
                rel = self._rel_pdf_path(pdf_path, pdf_root)
                report("cite_extract", i + 1, len(pdfs), rel)

                try:
                    pages = load_pdf_pages(Path(pdf_path), max_pages=max_pages)
                except Exception:
                    continue

                try:
                    for rec in iter_citation_sentences_from_pages(
                        pages,
                        pdf_label=rel,
                        stop_at_references=stop_at_references,
                    ):
                        if stats.citation_sentence_count >= int(max_citation_sentences):
                            break
                        d = rec.to_dict()
                        f_c.write(json.dumps(d, ensure_ascii=False) + "\n")
                        stats.citation_sentence_count += 1
                except Exception:
                    pass

                try:
                    for ref in iter_reference_entries_from_pages(pages, pdf_label=rel):
                        f_r.write(json.dumps(ref.to_dict(), ensure_ascii=False) + "\n")
                        stats.reference_count += 1
                except Exception:
                    pass

        os.replace(citations_tmp, self.citations_path)
        os.replace(refs_tmp, self.references_path)

        # 2) Embed citations
        report("cite_embed", 0, stats.citation_sentence_count, "")
        sentences: List[str] = []
        with open(self.citations_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                s = obj.get("sentence", "")
                if isinstance(s, str) and s.strip():
                    sentences.append(s.strip())
                else:
                    sentences.append("")

        def _embed_progress(done: int, total: int) -> None:
            report("cite_embed", done, total, "")

        vecs = embed_sentences(sentences, _embed_progress, cancel_cb)
        try:
            dim = int(getattr(vecs, "shape", [0, 0])[1])
        except Exception:
            dim = 0
        stats.dim = dim

        emb_tmp = self.embeddings_path + ".tmp.npy"
        meta_tmp = self.meta_path + ".tmp"

        np.save(emb_tmp, vecs.astype(np.float32, copy=False))
        os.replace(emb_tmp, self.embeddings_path)

        meta = {
            "pdf_root": os.path.abspath(pdf_root),
            "count": int(len(sentences)),
            "dim": int(dim),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(meta_tmp, self.meta_path)

        # 3) Build FAISS index
        report("cite_index", 0, int(len(sentences)), "")
        try:
            import faiss  # type: ignore
        except Exception as e:  # pragma: no cover
            raise CitationBankError(f"faiss import failed: {e}")

        try:
            index = faiss.IndexFlatIP(int(dim or 1))
            index.add(vecs.astype(np.float32, copy=False))
        except Exception as e:
            raise CitationBankError(f"faiss build failed: {e}")

        faiss_tmp = self.faiss_path + ".tmp"
        try:
            faiss.write_index(index, faiss_tmp)
            os.replace(faiss_tmp, self.faiss_path)
        except Exception as e:
            raise CitationBankError(f"faiss save failed: {e}")

        # 4) Manifest
        manifest_tmp = self.manifest_path + ".tmp"
        manifest = {
            "updated_at": int(time.time()),
            "pdf_root": os.path.abspath(pdf_root),
            "pdf_count": int(stats.pdf_count),
            "citation_sentence_count": int(stats.citation_sentence_count),
            "reference_count": int(stats.reference_count),
            "dim": int(stats.dim),
            "files": {self._rel_pdf_path(p, pdf_root): self._file_sig(p) for p in pdfs},
        }
        with open(manifest_tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(manifest_tmp, self.manifest_path)

        stats.seconds = float(time.time() - t0)
        report("cite_done", int(stats.citation_sentence_count), int(stats.citation_sentence_count), "")
        return stats

    def search(
        self,
        query: str,
        *,
        embed_query: Callable[[str], "object"],
        top_k: int = 8,
    ) -> List[CitationHit]:
        try:
            sess = self.create_session(embed_query=embed_query)
        except Exception:
            return []
        return sess.search(query, top_k=top_k)

    def create_session(self, *, embed_query: Callable[[str], "object"]) -> "CitationBankSearchSession":
        return CitationBankSearchSession(
            citations_path=self.citations_path,
            faiss_path=self.faiss_path,
            embed_query=embed_query,
        )


class CitationBankSearchSession:
    def __init__(self, *, citations_path: str, faiss_path: str, embed_query: Callable[[str], "object"]):
        if np is None:
            raise CitationBankError("numpy is required")
        if not os.path.exists(citations_path) or not os.path.exists(faiss_path):
            raise CitationBankError("citation index missing")

        try:
            import faiss  # type: ignore
        except Exception as e:  # pragma: no cover
            raise CitationBankError(f"faiss import failed: {e}")

        self._faiss = faiss
        self._embed_query = embed_query
        self._index = faiss.read_index(faiss_path)
        self._records = self._load_records(citations_path)

    @staticmethod
    def _load_records(citations_path: str) -> List[dict]:
        out: List[dict] = []
        with open(citations_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
        return out

    def search(self, query: str, *, top_k: int = 8) -> List[CitationHit]:
        query = (query or "").strip()
        if not query:
            return []
        records = self._records
        if not records:
            return []

        try:
            qv = self._embed_query(query)
            qv2 = np.asarray(qv, dtype=np.float32).reshape(1, -1)
        except Exception:
            return []

        try:
            k = max(1, min(int(top_k or 0), 50))
            scores, idxs = self._index.search(qv2, k)
        except Exception:
            return []

        out: List[CitationHit] = []
        for score, i in zip((scores[0] if len(scores) else []), (idxs[0] if len(idxs) else [])):
            try:
                ii = int(i)
            except Exception:
                continue
            if ii < 0 or ii >= len(records):
                continue
            r = records[ii]
            out.append(
                CitationHit(
                    score=float(score or 0.0),
                    pdf=str(r.get("pdf", "") or ""),
                    page=int(r.get("page", 0) or 0),
                    sentence=str(r.get("sentence", "") or ""),
                    citations=list(r.get("citations", []) or []),
                )
            )
        return out
