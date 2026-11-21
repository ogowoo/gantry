# -*- coding: utf-8 -*-
"""知识库时效与来源元数据。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.kb import KnowledgeBase


class KnowledgeFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_fresh_"))
        self.kbdir = self.tmp / "data" / "kb"
        self.kbdir.mkdir(parents=True)
        (self.kbdir / "old.md").write_text(
            "---\ntitle: Old\nexpires: 2000-01-01\nsource: legacy\n---\n\n## 内容\n旧知识。",
            encoding="utf-8")
        (self.kbdir / "new.md").write_text(
            "---\ntitle: New\nsource: official\nupdated: 2026-01-01\n---\n\n## 内容\n新知识。",
            encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_expired_document_is_filtered(self):
        hits = self.kb.search("知识", limit=10)
        titles = [h["title"] for h in hits]
        self.assertNotIn("Old", titles)
        self.assertIn("New", titles)

    def test_source_visible(self):
        hits = self.kb.search("新知识", limit=5)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source"], "official")


if __name__ == "__main__":
    unittest.main()
