import json
import os
import shutil
import tempfile
import unittest


class TestLibraryKind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="thw_test_")
        os.environ["TOPHUMANWRITING_DATA_DIR"] = self.tmp
        # Lazy-import after env var so get_settings_dir() uses the temp dir.
        import ai_word_detector as awd  # noqa: WPS433

        self.awd = awd
        try:
            self.awd._SETTINGS_DIR = None
        except Exception:
            pass
        self.lm = self.awd.LibraryManager()

    def tearDown(self):
        try:
            self.awd._SETTINGS_DIR = None
        except Exception:
            pass
        os.environ.pop("TOPHUMANWRITING_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_exemplar_library_has_kind(self):
        path = self.lm.create_library("finance_2026", kind="exemplar")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("kind"), "exemplar")

    def test_create_references_library_has_kind(self):
        path = self.lm.create_library("refs_finance", kind="references")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("kind"), "references")

    def test_list_libraries_infers_kind_from_name(self):
        # Legacy library file without `kind`.
        legacy_name = "citecheck_papers"
        legacy_path = self.lm.get_library_path(legacy_name)
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, "w", encoding="utf-8") as f:
            json.dump({"doc_count": 0, "word_doc_freq": {}}, f, ensure_ascii=False)

        libs = self.lm.list_libraries()
        hit = next((x for x in libs if x.get("name") == legacy_name), None)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.get("kind"), "references")

