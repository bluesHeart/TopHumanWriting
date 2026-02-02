# -*- coding: utf-8 -*-

import json
import os
import tempfile
import unittest

import fitz  # PyMuPDF

from aiwd.materials import MaterialsIndexer, build_material_doc


class TestMaterials(unittest.TestCase):
    def _make_pdf(self, path: str, pages: list[str]):
        doc = fitz.open()
        for text in pages:
            p = doc.new_page()
            p.insert_text((72, 72), text)
        doc.save(path)
        doc.close()

    def test_build_material_doc_extracts_headings_and_citations(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_root = os.path.join(td, "pdfs")
            os.makedirs(pdf_root, exist_ok=True)
            pdf_path = os.path.join(pdf_root, "paper.pdf")
            self._make_pdf(
                pdf_path,
                [
                    "1 Introduction\nThis paper studies market conditions (Smith, 2020).\n\n2 Data\nWe use CRSP.",
                    "References\nSmith, J. (2020). Title. Journal.",
                ],
            )

            doc = build_material_doc(pdf_path=pdf_path, pdf_root=pdf_root, llm=None)
            self.assertIsInstance(doc, dict)
            self.assertIn("headings", doc)
            heads = doc.get("headings", []) or []
            self.assertTrue(any("Introduction" in (h.get("text", "") or "") for h in heads))
            self.assertTrue(any((h.get("canonical", "") or "") == "introduction" for h in heads))

            cits = doc.get("citations", []) or []
            self.assertGreaterEqual(len(cits), 1)
            self.assertIn("citations", cits[0])

            refs = doc.get("references", []) or []
            self.assertGreaterEqual(len(refs), 1)

    def test_materials_indexer_build_writes_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            pdf_root = os.path.join(td, "pdfs")
            os.makedirs(pdf_root, exist_ok=True)
            pdf_path = os.path.join(pdf_root, "a.pdf")
            self._make_pdf(pdf_path, ["1 Introduction\nHello world.\n\n2 Data\nCRSP."])

            ix = MaterialsIndexer(data_dir=td, library_name="demo")
            stats = ix.build(pdf_root=pdf_root, llm=None, use_llm=False)
            self.assertGreaterEqual(stats.doc_count, 1)
            self.assertTrue(os.path.exists(ix.manifest_path))

            with open(ix.manifest_path, "r", encoding="utf-8") as f:
                mf = json.load(f)
            self.assertIsInstance(mf, dict)
            self.assertEqual(mf.get("library"), "demo")
            self.assertIn("docs", mf)


if __name__ == "__main__":
    unittest.main()

