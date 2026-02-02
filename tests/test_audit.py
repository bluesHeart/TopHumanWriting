# -*- coding: utf-8 -*-

import os
import tempfile
import unittest

import fitz  # PyMuPDF

from aiwd.audit import (
    analyze_repetition_starters,
    extract_pdf_pages_text,
    extract_scaffold,
    run_full_paper_audit,
)


class TestAudit(unittest.TestCase):
    def _make_pdf(self, pages):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = os.path.join(td.name, "paper.pdf")
        doc = fitz.open()
        for text in pages:
            p = doc.new_page()
            p.insert_text((72, 72), text)
        doc.save(path)
        doc.close()
        return path

    def test_extract_scaffold_en(self):
        s = "In this paper, we study how risk premia vary with market conditions."
        self.assertEqual(extract_scaffold(s, language="en"), "In this paper,")

    def test_extract_scaffold_zh(self):
        s = "本文  主要  研究：在不同市场状态下风险溢价如何变化。进一步，我们……"
        out = extract_scaffold(s, language="zh")
        self.assertTrue(out.startswith("本文主要研究"))
        self.assertLessEqual(len(out), 16)

    def test_extract_pdf_pages_text(self):
        pdf = self._make_pdf(["Hello page1", "Hello page2"])
        pages = extract_pdf_pages_text(pdf)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0]["page"], 1)
        self.assertEqual(pages[1]["page"], 2)

    def test_analyze_repetition_starters_en(self):
        sents = [
            {"id": 1, "text": "This paper studies how risk premia vary with market conditions in detail."},
            {"id": 2, "text": "This paper studies how risk premia vary with volatility regimes in detail."},
            {"id": 3, "text": "This paper studies how risk premia vary with investor sentiment in detail."},
        ]
        rep = analyze_repetition_starters(sents, language="en", min_repeat=3)
        self.assertIn(1, rep)
        self.assertIn(2, rep)
        self.assertIn(3, rep)

    def test_run_full_paper_audit_low_alignment(self):
        pdf = self._make_pdf(
            [
                "This paper studies how risk premia vary with market conditions. This paper studies how risk premia vary with volatility regimes.",
                "This paper studies how risk premia vary with investor sentiment in detail.",
            ]
        )

        def stub_search(q, top_k):
            return [
                (0.2, {"pdf": "ex.pdf", "page": 3, "text": "In this paper, we investigate ..."})
            ]

        r = run_full_paper_audit(
            paper_pdf_path=pdf,
            exemplar_library="demo",
            search_exemplars=stub_search,
            include_style=False,
            include_syntax=False,
            include_repetition=True,
            low_alignment_threshold=0.5,
            max_sentences=30,
            top_k=1,
        )
        self.assertIsInstance(r, dict)
        self.assertIn("items", r)
        self.assertGreaterEqual(len(r["items"]), 1)
        # Ensure at least one low_alignment issue exists.
        has_low = False
        for it in r["items"]:
            for iss in it.get("issues", []) or []:
                if iss.get("issue_type") == "low_alignment":
                    has_low = True
                    break
        self.assertTrue(has_low)


if __name__ == "__main__":
    unittest.main()

