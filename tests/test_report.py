# -*- coding: utf-8 -*-

import unittest

from aiwd.report import audit_to_markdown


class TestReport(unittest.TestCase):
    def test_audit_to_markdown_basic(self):
        r = {
            "meta": {"created_at": 1700000000, "language": "en", "paper_pages": 10, "truncated": False},
            "summary": {"sentence_total": 100, "sentence_scored": 50, "low_alignment_sentences": 5},
            "items": [
                {"id": 1, "page": 1, "text": "A sentence.", "issues": [{"issue_type": "low_alignment"}], "alignment": {"pct": 20}},
            ],
            "llm_reviews": {
                "sentence_alignment": {
                    "items": [
                        {
                            "id": 1,
                            "diagnosis": [{"problem": "x", "suggestion": "y", "evidence": [{"id": "S1_E1", "pdf": "ex.pdf", "page": 2, "quote": "In this paper"}]}],
                            "templates": [{"text": "In this paper, we …", "evidence": [{"id": "S1_E1", "pdf": "ex.pdf", "page": 2, "quote": "In this paper"}]}],
                        }
                    ]
                }
            },
            "llm_usage": {"calls": 1, "cost_per_1m_tokens": 10.0, "max_cost": 5.0, "total_tokens": 1000},
        }
        md = audit_to_markdown(r)
        self.assertIsInstance(md, str)
        self.assertIn("全稿体检报告", md)
        self.assertIn("对齐度低句子", md)


if __name__ == "__main__":
    unittest.main()
