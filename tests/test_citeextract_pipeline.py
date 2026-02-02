import os
import sys
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


from aiwd.citeextract.pipeline import iter_citation_sentences_from_pages  # noqa: E402


class TestCiteExtractPipeline(unittest.TestCase):
    def test_citations_before_references_on_same_page_are_kept(self):
        pages = [
            "\n".join(
                [
                    "Introduction",
                    "According to Smith (2020), liquidity affects returns.",
                    "References",
                    "Smith, J. (2020). A Paper Title. Journal of Testing.",
                ]
            )
        ]
        recs = list(iter_citation_sentences_from_pages(pages, pdf_label="main.pdf", stop_at_references=True))
        self.assertTrue(recs, "should extract citation sentences before References heading on same page")
        self.assertTrue(any("Smith" in r.sentence and "2020" in r.sentence for r in recs))


if __name__ == "__main__":
    unittest.main()

