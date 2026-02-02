# -*- coding: utf-8 -*-

import unittest

from aiwd.llm_review import LLMBudget, review_outline_structure, review_paragraph_alignment, review_sentence_alignment


class StubLLM:
    def __init__(self, content: str, *, prompt_tokens: int = 10, completion_tokens: int = 20):
        self._content = content
        self._usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens}

    def chat(self, *args, **kwargs):
        return 200, {"choices": [{"message": {"content": self._content}}], "usage": dict(self._usage)}


class TestLLMReview(unittest.TestCase):
    def test_sentence_alignment_review_accepts_stable_evidence_ids(self):
        audit_items = [
            {
                "id": 12,
                "text": "This paper studies how risk premia vary with market conditions.",
                "issues": [{"issue_type": "low_alignment", "severity": "warning"}],
                "alignment": {
                    "score": 0.1,
                    "pct": 10,
                    "exemplars": [
                        {"pdf": "ex.pdf", "page": 3, "text": "In this paper, we study how risk premia vary with market conditions."},
                    ],
                },
            }
        ]

        llm = StubLLM(
            content='{"items":[{"id":12,"diagnosis":[{"problem":"Too plain","suggestion":"Use a formal opener","evidence":[{"id":"S12_E1","quote":"In this paper, we study"}]}],"templates":[{"text":"In this paper, we ...","evidence":[{"id":"S12_E1","quote":"In this paper, we study"}]}]}]}'
        )
        budget = LLMBudget(max_cost=5.0, cost_per_1m_tokens=0.2)
        r = review_sentence_alignment(audit_items=audit_items, budget=budget, llm=llm, top_n=5, batch_size=1, evidence_top_k=1)
        self.assertFalse(r.get("skipped", False))
        items = r.get("items", [])
        self.assertEqual(len(items), 1)
        self.assertEqual(int(items[0].get("id", -1)), 12)

    def test_outline_review_requires_evidence_quote(self):
        paper_heads = [{"page": 1, "level": 1, "text": "1 Introduction"}]
        exemplar_outlines = [{"seq": "introduction > data > results > conclusion", "example": {"pdf_rel": "a.pdf"}}]

        llm = StubLLM(
            content='{"summary":"ok","issues":[{"problem":"missing","detail":"no data","suggestion":"add data section","evidence":[{"id":"O1","quote":"introduction > data"}]}],"section_template_hints":[]}'
        )
        budget = LLMBudget(max_cost=5.0, cost_per_1m_tokens=0.2)
        r = review_outline_structure(paper_headings=paper_heads, exemplar_outlines=exemplar_outlines, budget=budget, llm=llm)
        self.assertFalse(r.get("skipped", False))
        self.assertIn("issues", r)

    def test_paragraph_review_validates_evidence(self):
        paras = [{"id": "P0", "page": 1, "text": "This is a long paragraph. " * 40, "lang": "en", "sentences": []}]

        def rag_search(q: str, k: int):
            return [(0.8, {"pdf": "ex.pdf", "page": 2, "text": "To this end, we propose a simple approach."})]

        llm = StubLLM(
            content='{"items":[{"paragraph_id":"P0","page":1,"diagnosis":[{"problem":"Too long","suggestion":"Split and add topic sentence","evidence":[{"id":"P0_E1","quote":"To this end, we"}]}],"templates":[{"text":"To this end, we ...","evidence":[{"id":"P0_E1","quote":"To this end, we"}]}]}]}'
        )
        budget = LLMBudget(max_cost=5.0, cost_per_1m_tokens=0.2)
        r = review_paragraph_alignment(paper_paragraphs=paras, rag_search=rag_search, budget=budget, llm=llm, top_n=2, batch_size=1, evidence_top_k=1)
        self.assertFalse(r.get("skipped", False))
        self.assertEqual(len(r.get("items", [])), 1)


if __name__ == "__main__":
    unittest.main()
