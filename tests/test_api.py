# -*- coding: utf-8 -*-

import os
import tempfile
import unittest
from unittest.mock import MagicMock

from tophumanwriting import TopHumanWriting
from tophumanwriting.library import LibraryStatus


class TestTopHumanWritingAPI(unittest.TestCase):
    def test_slugify_default_library_name(self):
        with tempfile.TemporaryDirectory() as td:
            ex = os.path.join(td, "My Exemplar Library (2026)")
            os.makedirs(ex, exist_ok=True)
            thw = TopHumanWriting(exemplars=ex, data_dir=td)
            self.assertTrue(thw.library_name)
            self.assertNotIn(" ", thw.library_name)

    def test_run_auto_fit_profile_params(self):
        with tempfile.TemporaryDirectory() as td:
            ex = os.path.join(td, "reference_papers")
            os.makedirs(ex, exist_ok=True)

            thw = TopHumanWriting(exemplars=ex, data_dir=td)
            thw.ensure_semantic_model = MagicMock(return_value="")  # avoid network/model dependency

            thw._builder.status = MagicMock(
                return_value=LibraryStatus(
                    name=thw.library_name,
                    pdf_root=ex,
                    rag_ready=False,
                    cite_ready=False,
                    materials_ready=False,
                    vocab_ready=False,
                )
            )
            thw._builder.build = MagicMock(return_value=thw._builder.status.return_value)

            thw._runner.run = MagicMock(return_value=(os.path.join(td, "export"), {"ok": True}))

            out = thw.run("paper.pdf", profile="cheap", ensure_models=False, use_llm=False, max_llm_tokens=12345)
            self.assertTrue(thw._builder.build.called)
            self.assertEqual(out.export_dir, os.path.join(td, "export"))

            cfg = thw._runner.run.call_args[0][0]
            self.assertEqual(cfg.exemplar_library, thw.library_name)
            self.assertEqual(cfg.top_k, 12)
            self.assertEqual(cfg.paragraph_top_k, 12)
            self.assertEqual(cfg.max_pairs, 300)
            self.assertEqual(cfg.max_sentences, 1600)
            self.assertEqual(cfg.max_llm_tokens, 12345)
            self.assertFalse(cfg.use_llm)


if __name__ == "__main__":
    unittest.main()
