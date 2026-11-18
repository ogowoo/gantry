# -*- coding: utf-8 -*-
"""本地知识库测试：切块、front-matter、中文检索（trigram）、增量重建、降级路径。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.kb import KnowledgeBase, _parse_front_matter, _split_sections  # noqa: E402

VPN_DOC = """---
title: VPN 连不上
tags: vpn, 网络, 远程办公
category: network
---

# VPN 连不上怎么办

## 症状
客户端提示"无法建立隧道"，或者一直转圈。

## 排查步骤
1. 确认家里宽带能打开门户网站。
2. 换手机热点试一次，排除运营商问题。
3. 在客户端里删除旧配置后重新导入。
4. 公司 VPN 网关地址是 vpn.example.com，端口 443。

## 升级
以上都不行请联系 IT 服务台，分机 8000。
"""

PRINTER_DOC = """---
title: 打印机无法打印
tags: 打印机, 驱动
---

# 打印机无法打印

## 驱动问题
在"设备和打印机"里删除设备，重新添加，驱动选 Universal Print Driver v4。

## 脱机状态
右键打印机 → 查看正在打印 → 取消所有文档，然后重启 Print Spooler 服务。
"""


class ChunkTests(unittest.TestCase):
    def test_front_matter(self):
        meta, body = _parse_front_matter(VPN_DOC)
        self.assertEqual(meta["title"], "VPN 连不上")
        self.assertEqual(meta["tags"], ["vpn", "网络", "远程办公"])
        self.assertTrue(body.startswith("# VPN 连不上怎么办"))

    def test_split_keeps_heading_path(self):
        _, body = _parse_front_matter(VPN_DOC)
        secs = _split_sections(body)
        paths = [h for h, _ in secs]
        self.assertTrue(any("排查步骤" in p for p in paths), paths)
        joined = "\n".join(t for _, t in secs)
        self.assertIn("vpn.example.com", joined)

    def test_no_front_matter(self):
        meta, body = _parse_front_matter("# 纯标题\n内容")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# 纯标题\n内容")


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_test_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN_DOC, encoding="utf-8")
        (kbdir / "printer.md").write_text(PRINTER_DOC, encoding="utf-8")
        (kbdir / "sub").mkdir()
        (kbdir / "sub" / "wifi.md").write_text(
            "---\ntitle: WiFi 掉线\n---\n\n## 处理\n换 5GHz 频段，或重启 AP。", encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.stats = self.kb.reindex()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_reindex_counts(self):
        self.assertEqual(self.stats["documents"], 3)
        self.assertGreater(self.stats["chunks"], 3)
        self.assertTrue(self.stats["fts"], "本机 SQLite 应支持 FTS5 trigram")

    def test_chinese_query_hits(self):
        """trigram 的关键价值：中文整句也能搜到（unicode61 会直接 0 命中）。"""
        hits = self.kb.search("VPN 连不上", 3)
        self.assertTrue(hits, "中文查询没命中")
        self.assertEqual(hits[0]["title"], "VPN 连不上")
        # 命中最高的通常是「排查步骤」（标题路径里就带 VPN/连不上），症状段也要在结果里
        joined = "\n".join(h["text"] for h in hits)
        self.assertIn("隧道", joined)

    def test_chinese_partial(self):
        hits = self.kb.search("打印机脱机", 3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "打印机无法打印")

    def test_english_and_path(self):
        hits = self.kb.search("Universal Print Driver", 3)
        self.assertTrue(hits)
        self.assertIn("printer.md", hits[0]["path"])

    def test_nested_dir_indexed(self):
        hits = self.kb.search("5GHz 频段", 3)
        self.assertTrue(hits)
        self.assertIn("sub/wifi.md", hits[0]["path"])

    def test_score_range(self):
        hits = self.kb.search("分机 8000", 3)
        self.assertTrue(hits)
        self.assertTrue(0 < hits[0]["score"] <= 1.0, hits[0]["score"])

    def test_empty_query(self):
        self.assertEqual(self.kb.search("", 3), [])
        self.assertEqual(self.kb.search("   ", 3), [])

    def test_incremental_reindex_skips(self):
        again = self.kb.reindex()
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 3)
        # 改一个文件：只该 updated 1 个
        (self.tmp / "data" / "kb" / "printer.md").write_text(
            PRINTER_DOC + "\n## 补充\n纸盒缺纸也会导致不打印。", encoding="utf-8")
        third = self.kb.reindex()
        self.assertEqual(third["updated"], 1)
        self.assertEqual(third["skipped"], 2)
        self.assertTrue(self.kb.search("纸盒缺纸", 3))

    def test_removed_file_dropped(self):
        (self.tmp / "data" / "kb" / "printer.md").unlink()
        res = self.kb.reindex()
        self.assertEqual(res["removed"], 1)
        self.assertFalse([h for h in self.kb.search("Universal Print Driver", 3)
                          if h["path"] == "printer.md"])

    def test_context_for(self):
        ctx = self.kb.context_for("VPN 网关地址", 2)
        self.assertIn("《VPN 连不上》", ctx)
        self.assertIn("vpn.example.com", ctx)
        self.assertNotIn("Print Spooler", ctx)

    def test_stats(self):
        st = self.kb.stats()
        self.assertEqual(st["documents"], 3)
        self.assertGreater(st["chars"], 0)

    def test_like_fallback_still_works(self):
        """FTS 不可用时（老 SQLite）也要能搜到 —— 只降排序，不降功能。"""
        self.kb.fts = False
        con = self.kb.db()
        self.kb.reindex(force=True)
        hits = self.kb.search("VPN 连不上", 3)
        self.assertTrue(hits, "降级路径没命中")
        self.assertEqual(hits[0]["title"], "VPN 连不上")
        del con


class ScoreTests(unittest.TestCase):
    """覆盖率打分的关键性质：相关的高、只沾边词的低、无关的空。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_score_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN_DOC, encoding="utf-8")
        (kbdir / "printer.md").write_text(PRINTER_DOC, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_exact_section_scores_highest(self):
        hits = self.kb.search("VPN 连不上", 4)
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0]["score"], 0.7, hits[0]["score"])
        self.assertIn("排查", hits[0]["heading"] + hits[0]["text"])

    def test_scores_are_not_all_one(self):
        hits = self.kb.search("VPN 连不上", 4)
        self.assertGreater(len(hits), 1)
        scores = [h["score"] for h in hits]
        self.assertGreater(max(scores) - min(scores), 0.05,
                           f"分数区分度太低（{scores}）——阈值会失效")

    def test_unrelated_query_returns_nothing(self):
        self.assertEqual(self.kb.search("火星怎么去", 4), [])

    def test_partial_coverage_scores_lower(self):
        """多词 query 里只覆盖一部分 → 分数明显低于全覆盖（这才让阈值有意义）。"""
        full = self.kb.search("打印机 脱机", 4)          # 两个词都有
        partial = self.kb.search("打印机 脱机 报错 驱动 纸张", 4)  # 只有前几个词有
        self.assertTrue(full and partial)
        self.assertGreaterEqual(full[0]["score"], 0.7)
        self.assertLess(partial[0]["score"], full[0]["score"] + 1e-9)
        self.assertLess(partial[0]["score"], 1.0)

    def test_single_term_fully_covered_is_high(self):
        """单词 query 覆盖率天然 1.0 —— 文档里确实有这个词，判高分是对的。"""
        hits = self.kb.search("打印机", 4)
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0]["score"], 0.9)

    def test_pick_hits_threshold(self):
        from gantry.teams_logic import pick_hits
        hits = self.kb.search("VPN 连不上", 4)
        good = pick_hits(hits, 0.7)
        self.assertTrue(good)
        self.assertTrue(all(h["score"] >= 0.7 for h in good))
        self.assertEqual(pick_hits(hits, 1.01), [])


class ForceReindexTests(unittest.TestCase):
    """force 重建曾经必炸：FTS 行没先删 → rowid 冲突（sqlite3.IntegrityError）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_force_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN_DOC, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_force_twice_is_stable(self):
        first = self.kb.reindex(force=True)
        second = self.kb.reindex(force=True)
        self.assertEqual(first["documents"], 1)
        self.assertEqual(second["documents"], 1)
        self.assertEqual(first["chunks"], second["chunks"], "force 重建不该累积分块")
        # 检索仍然可用（说明 FTS 与 chunks 行数一致）
        self.assertTrue(self.kb.search("VPN 连不上", 3))

    def test_force_keeps_single_copy(self):
        before = self.kb.stats()["chunks"]
        self.kb.reindex(force=True)
        self.assertEqual(self.kb.stats()["chunks"], before)


if __name__ == "__main__":
    unittest.main()
