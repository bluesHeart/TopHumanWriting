import os
import sys
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


from aiwd.cite_check import extract_reference_title, match_reference_entry  # noqa: E402
from aiwd.citeextract.references import ReferenceEntry  # noqa: E402


class TestCiteCheckHelpers(unittest.TestCase):
    def test_extract_reference_title_prefers_quoted_span(self):
        ref = 'Smith, J. (2020). "A Very Long Paper Title That Should Be Extracted". Journal of Testing.'
        self.assertEqual(extract_reference_title(ref), "A Very Long Paper Title That Should Be Extracted")

    def test_extract_reference_title_after_year(self):
        ref = "Smith, J. (2020). A Paper Title Without Quotes. Journal of Testing."
        title = extract_reference_title(ref)
        self.assertTrue("A Paper Title Without Quotes" in title)

    def test_match_reference_entry_author_year(self):
        refs = [
            ReferenceEntry(index=1, page=10, pdf="main.pdf", reference="Foo, A. (2019). X.", authors="Foo, A.", year="2019"),
            ReferenceEntry(index=2, page=10, pdf="main.pdf", reference="Smith, J. and Doe, K. (2020). Y.", authors="Smith, J. and Doe, K.", year="2020"),
            ReferenceEntry(index=3, page=10, pdf="main.pdf", reference="Smith, J. (2018). Z.", authors="Smith, J.", year="2018"),
        ]
        hit = match_reference_entry(cited_author="Smith", cited_year="2020", references=refs)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.year, "2020")
        self.assertIn("Smith", hit.authors)

    def test_match_reference_entry_short_surname_boundary(self):
        refs = [
            ReferenceEntry(index=1, page=10, pdf="main.pdf", reference="Li, M. (2020). X.", authors="Li, M.", year="2020"),
            ReferenceEntry(index=2, page=10, pdf="main.pdf", reference="Liu, M. (2020). Y.", authors="Liu, M.", year="2020"),
        ]
        hit = match_reference_entry(cited_author="Li", cited_year="2020", references=refs)
        self.assertIsNotNone(hit)
        self.assertIn("Li", hit.authors)

    def test_match_reference_entry_cjk_author(self):
        refs = [
            ReferenceEntry(index=1, page=10, pdf="main.pdf", reference="张三（2020）. 一篇论文. 期刊.", authors="张三", year="2020"),
            ReferenceEntry(index=2, page=10, pdf="main.pdf", reference="李四（2020）. 另一篇论文. 期刊.", authors="李四", year="2020"),
        ]
        hit = match_reference_entry(cited_author="张三", cited_year="2020", references=refs)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.authors, "张三")


if __name__ == "__main__":
    unittest.main()
