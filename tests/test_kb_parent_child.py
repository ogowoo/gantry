# -*- coding: utf-8 -*-
"""通用父子块与元数据：child 可召回，parent 用于最终上下文。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.kb import KnowledgeBase

DOC = """---
title: 合同违约责任
law: 中华人民共和国民法典
article: 第五百八十四条
source: 全国人大
category: 法律
---

# 合同编

## 违约责任

当事人一方不履行合同义务或者履行合同义务不符合约定，造成对方损失的，损失赔偿额应当相当于因违约所造成的损失。

包括合同履行后可以获得的利益；但是，不得超过违约一方订立合同时预见到或者应当预见到的因违约可能造成的损失。

本条第一款规定的损失赔偿额，应当以实际损失为基础，兼顾合同履行情况、当事人过错程度以及预期利益等综合因素确定。
""" + ("补充说明：" + "违约责任应当依法承担。" * 60)


class ParentChildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_parent_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "contract.md").write_text(DOC, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.stats = self.kb.reindex()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parent_rows_and_child_parent_text(self):
        con = self.kb.db()
        parents = con.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE chunk_kind='parent'").fetchone()["n"]
        children = con.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE chunk_kind='child'").fetchone()["n"]
        self.assertGreaterEqual(parents, 1)
        self.assertGreater(children, 1)
        self.assertEqual(self.stats["parents"], parents)
        self.assertGreaterEqual(self.stats["chunks"], children)

        hits = self.kb.search("违约损失赔偿", 3)
        self.assertTrue(hits)
        hit = next(h for h in hits if h.get("parent_id"))
        self.assertTrue(hit.get("parent_text"))
        self.assertIn("第五百八十四条", hit["meta"]["front"]["article"])
        self.assertEqual(hit["chunk_kind"], "child")

    def test_context_uses_parent_and_metadata(self):
        ctx = self.kb.context_for("违约责任", 2, max_chars=6000)
        self.assertIn("合同违约责任", ctx)
        self.assertIn("第五百八十四条", ctx)   # source/category 或 parent 正文
        self.assertIn("违约", ctx)


if __name__ == "__main__":
    unittest.main()
