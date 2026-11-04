# -*- coding: utf-8 -*-
"""向量检索层测试（全部离线：HashEmbedder，不依赖 18085 上的真模型）。

覆盖：hashing 后端、索引增量/强制重建、余弦打分、混合融合（向量独有 / 词面独有都能进来）、
后端选择（off/hash/http/auto 探测失败退回 None）、插件 mode 分发。
"""
import asyncio
import importlib.util
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.kb import KnowledgeBase  # noqa: E402
from gantry.kb_semantic import (  # noqa: E402
    HashEmbedder, HttpEmbedder, SemanticIndex, _cosine, build_embedder,
)

VPN = """---
title: VPN 连不上怎么办
tags: vpn, 网络
---

# VPN 连不上怎么办

## 症状
客户端提示"无法建立隧道"，或者一直转圈。

## 排查步骤
1. 换手机热点试一次。
2. 网关 vpn.example.com，端口 443。
"""

WIFI = """---
title: Wi-Fi 802.1X 认证失败
tags: wifi, 网络
---

# Wi-Fi 802.1X 认证失败

## 症状
连接公司无线一直提示认证失败。

## 排查步骤
检查证书是否过期，重新输入域账号密码。
"""


class HashEmbedderTests(unittest.TestCase):
    def test_dim_and_norm(self):
        emb = HashEmbedder(64)
        v = emb.embed(["VPN 连不上怎么办"])[0]
        self.assertEqual(len(v), 64)
        norm = math.sqrt(sum(x * x for x in v))
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_deterministic(self):
        emb = HashEmbedder(32)
        a = emb.embed(["无线认证失败"])[0]
        b = emb.embed(["无线认证失败"])[0]
        self.assertEqual(a, b)

    def test_related_closer_than_unrelated(self):
        emb = HashEmbedder(256)
        q, near, far = emb.embed(["无线认证失败", "Wi-Fi 认证失败怎么办", "打印机脱机"])
        self.assertGreater(_cosine(q, near), _cosine(q, far))


class EmbedderChoiceTests(unittest.TestCase):
    def test_off(self):
        self.assertIsNone(build_embedder({"backend": "off"}))

    def test_hash(self):
        emb = build_embedder({"backend": "hash", "dim": 128})
        self.assertIsInstance(emb, HashEmbedder)
        self.assertEqual(emb.dim, 128)

    def test_http_forced_no_probe(self):
        emb = build_embedder({"backend": "http", "base_url": "http://127.0.0.1:9/v1",
                              "model": "m"})
        self.assertIsInstance(emb, HttpEmbedder)

    def test_auto_falls_back_when_unreachable(self):
        """auto 探测失败要返回 None（调用方退回 FTS），不能抛异常。"""
        self.assertIsNone(build_embedder({"backend": "auto",
                                          "base_url": "http://127.0.0.1:9/v1",
                                          "model": "m"}))


class SemanticIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_vec_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN, encoding="utf-8")
        (kbdir / "wifi.md").write_text(WIFI, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()
        self.idx = SemanticIndex(self.kb, HashEmbedder(256))

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_covers_all_chunks(self):
        res = self.idx.build()
        self.assertTrue(res["complete"])
        self.assertEqual(res["vectorised"], res["chunks"])
        self.assertEqual(res["dim"], 256)

    def test_build_is_incremental(self):
        self.idx.build()
        again = self.idx.build()
        self.assertEqual(again["added"], 0, "第二次不该重复算")

    def test_build_only_new_chunks(self):
        self.idx.build()
        (self.tmp / "data" / "kb" / "vpn.md").write_text(
            VPN + "\n## 补充\n分机 8000。\n", encoding="utf-8")
        self.kb.reindex()
        res = self.idx.build()
        self.assertGreaterEqual(res["added"], 1)
        self.assertLess(res["added"], res["chunks"])
        self.assertTrue(res["complete"])

    def test_force_rebuild(self):
        self.idx.build()
        res = self.idx.build(force=True)
        self.assertEqual(res["added"], res["chunks"])

    def test_search_scores_sorted(self):
        self.idx.build()
        hits = self.idx.search("无线认证失败", 3)
        self.assertTrue(hits)
        self.assertEqual([h["via"] for h in hits], ["vector"] * len(hits))
        scores = [h["score"] for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0.0 <= s <= 1.0 for s in scores))

    def test_hybrid_has_both_fields(self):
        self.idx.build()
        hits = self.idx.hybrid("VPN 隧道连不上", 3, w_vec=0.7, w_cov=0.3)
        self.assertTrue(hits)
        for h in hits:
            self.assertIn("vec", h)
            self.assertIn("coverage", h)
            self.assertIn(h["via"], ("vector", "fts", "both"))

    def test_hybrid_score_formula(self):
        self.idx.build()
        hits = self.idx.hybrid("VPN 连不上", 3, w_vec=0.75, w_cov=0.25)
        top = hits[0]
        expect = round(0.75 * top["vec"] + 0.25 * top["coverage"], 4)
        self.assertAlmostEqual(top["score"], expect, places=4)

    def test_empty_query(self):
        self.idx.build()
        self.assertEqual(self.idx.search("", 3), [])
        self.assertEqual(self.idx.hybrid("  ", 3), [])

    def test_search_without_index(self):
        self.assertEqual(self.idx.search("任意", 3), [])


class PluginModeTests(unittest.TestCase):
    """插件层：mode=fts/vector/hybrid 的分发与 kb_embed 工具。"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "kb_plugin_v", ROOT / "plugins" / "kb" / "plugin.py")
        self.plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.plugin)
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_vec_plugin_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN, encoding="utf-8")
        (kbdir / "wifi.md").write_text(WIFI, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()
        self.idx = SemanticIndex(self.kb, HashEmbedder(256))
        self._orig = self.plugin._kb
        self.plugin._kb = lambda ctx: self.kb

    def tearDown(self):
        self.plugin._kb = self._orig
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctx(self, **cfg):
        class C:
            def __init__(self):
                self.config = dict(cfg)
                self.state = {}
                self.logs = []
                self.tools = {}
                self.apis = {}
                self.panels = []

            def log(self, m, *a, **k):
                self.logs.append(str(m))

            def save_state(self, **kw):
                self.state.update(kw)

            def register_tool(self, n, f, d, args=None, **k):
                self.tools[n] = f

            def register_api(self, m, p, f):
                self.apis[(m, p)] = f

            def register_panel(self, p):
                self.panels.append(p)

        c = C()
        # 直接把向量索引挂上（插件里 _sem(ctx) 看到非 "missing" 就直接用）
        c._sem = self.idx
        return c

    def run_coro(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_mode_fts(self):
        ctx = self._ctx()
        self.plugin.setup(ctx)
        res = self.run_coro(ctx.tools["kb_search"]("VPN 连不上", 3, None, "fts"))
        self.assertEqual(res["backend"], "fts5-trigram")

    def test_mode_vector(self):
        ctx = self._ctx()
        self.plugin.setup(ctx)
        self.idx.build()
        res = self.run_coro(ctx.tools["kb_search"]("无线认证失败", 3, None, "vector"))
        self.assertIn("vector", res["backend"])
        self.assertTrue(res["hits"])

    def test_mode_auto_uses_hybrid_when_indexed(self):
        ctx = self._ctx()
        self.plugin.setup(ctx)
        self.idx.build()
        res = self.run_coro(ctx.tools["kb_search"]("VPN 连不上", 3))
        self.assertIn("hybrid", res["backend"])

    def test_kb_embed_tool(self):
        ctx = self._ctx()
        self.plugin.setup(ctx)
        res = self.run_coro(ctx.tools["kb_embed"]())
        self.assertTrue(res.get("ok"), res)
        self.assertGreater(res.get("vectorised", 0), 0)

    def test_vector_stats_api(self):
        ctx = self._ctx()
        self.plugin.setup(ctx)
        self.idx.build()
        res = self.run_coro(ctx.apis[("GET", "api/vector-stats")]())
        self.assertTrue(res["available"])
        self.assertEqual(res["model"], self.idx.embedder.name)


class VectorPathParityTests(unittest.TestCase):
    """两条实现（numpy 快路径 / 纯 Python）排序必须一致。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_np_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN, encoding="utf-8")
        (kbdir / "wifi.md").write_text(WIFI, encoding="utf-8")
        self.kb = KnowledgeBase(self.tmp)
        self.kb.reindex()
        self.idx = SemanticIndex(self.kb, HashEmbedder(256))
        self.idx.build()

    def tearDown(self):
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_numpy_and_python_agree(self):
        with_np = self.idx.search("无线认证失败", 3)
        import builtins

        real = builtins.__import__

        def no_numpy(name, *a, **k):
            if name == "numpy":
                raise ImportError("blocked for test")
            return real(name, *a, **k)

        builtins.__import__ = no_numpy
        try:
            without = self.idx.search("无线认证失败", 3)
        finally:
            builtins.__import__ = real
        self.assertEqual([h["id"] for h in with_np], [h["id"] for h in without])
        self.assertTrue(with_np)


if __name__ == "__main__":
    unittest.main()
