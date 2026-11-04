# -*- coding: utf-8 -*-
"""kb 插件的离线测试：清单、注册、检索/重建工具、HTTP 接口、引用格式。

插件按文件路径加载（和 teams-bot 一样），ctx 用假对象；知识库用临时目录 + 真实
`gantry.kb.KnowledgeBase`，因此检索行为是被真实覆盖的（不是假 KB）。
"""
import asyncio
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("kb_plugin", ROOT / "plugins" / "kb" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

VPN = """---
title: VPN 连不上怎么办
tags: vpn, 网络
---

# VPN 连不上怎么办

## 症状
客户端提示"无法建立隧道"，或者一直转圈。

## 排查步骤
1. 换手机热点试一次。
2. 网关地址 vpn.example.com，端口 443。
3. 仍不行找 IT 服务台，分机 8000。
"""


class FakeCtx:
    def __init__(self, **config):
        self.config = dict(config)
        self.state: dict = {}
        self.logs: list[str] = []
        self.tools: dict[str, object] = {}
        self.apis: dict[tuple[str, str], object] = {}
        self.panels: list[dict] = []

    def log(self, msg, *a, **k):
        self.logs.append(str(msg))

    def save_state(self, **kw):
        self.state.update(kw)

    def register_tool(self, name, fn, desc, args=None, **k):
        self.tools[name] = fn

    def register_api(self, method, path, fn):
        self.apis[(method, path)] = fn

    def register_panel(self, panel):
        self.panels.append(panel)


class _Req:
    def __init__(self, params=None, body=None):
        self.query_params = params or {}
        self._body = body or {}

    async def json(self):
        return self._body


class KbPluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kb_plugin_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "vpn.md").write_text(VPN, encoding="utf-8")
        from gantry.kb import KnowledgeBase

        self.real_kb = KnowledgeBase(self.tmp)
        self.real_kb.reindex()
        self._orig = plugin._kb
        plugin._kb = lambda ctx: self.real_kb
        self.ctx = FakeCtx()

    def tearDown(self):
        plugin._kb = self._orig
        self.real_kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_coro(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    # ── 清单与注册 ──
    def test_manifest(self):
        d = json.loads((ROOT / "plugins" / "kb" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(d["name"], "kb")
        tools = d["gantry"]["contributes"]["tools"]
        for t in ("kb_search", "kb_reindex", "kb_stats"):
            self.assertIn(t, tools)
        self.assertIn("tools", d["gantry"]["permissions"])
        self.assertIn("api", d["gantry"]["permissions"])

    def test_setup_registers_everything(self):
        plugin.setup(self.ctx)
        for t in ("kb_search", "kb_reindex", "kb_stats"):
            self.assertIn(t, self.ctx.tools)
        for path in ("api/search", "api/reindex", "api/stats", "api/docs"):
            self.assertIn(("GET", path) if path != "api/reindex" else ("POST", path),
                          self.ctx.apis)
        self.assertTrue(self.ctx.panels, "应注册一个状态面板")
        self.assertEqual(self.ctx.panels[0]["id"], "kb-status")
        plugin.teardown(self.ctx)

    # ── 工具行为 ──
    def test_search_returns_hits_with_cite(self):
        plugin.setup(self.ctx)
        fn = self.ctx.tools["kb_search"]
        res = self.run_coro(fn("VPN 连不上", 3))
        self.assertGreater(res["count"], 0)
        hit = res["hits"][0]
        self.assertIn("VPN", hit["title"])
        self.assertIn("《", hit["cite"], "引用格式应形如《标题》 › 小节")
        self.assertGreater(hit["score"], 0)
        self.assertEqual(res["backend"], "fts5-trigram")

    def test_search_empty_query(self):
        plugin.setup(self.ctx)
        res = self.run_coro(self.ctx.tools["kb_search"]("", 3))
        self.assertEqual(res["count"], 0)
        self.assertIn("query", res["note"])

    def test_min_score_filters(self):
        plugin.setup(self.ctx)
        res = self.run_coro(self.ctx.tools["kb_search"]("VPN 连不上", 3, 1.01))
        self.assertEqual(res["count"], 0, "阈值应该能筛掉")

    def test_reindex_tool(self):
        plugin.setup(self.ctx)
        res = self.run_coro(self.ctx.tools["kb_reindex"](True))
        self.assertEqual(res["documents"], 1)
        self.assertGreaterEqual(res["chunks"], 1)

    def test_stats_tool(self):
        plugin.setup(self.ctx)
        st = self.run_coro(self.ctx.tools["kb_stats"]())
        self.assertEqual(st["documents"], 1)
        self.assertTrue(st["fts"])

    # ── HTTP 接口 ──
    def test_api_search(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("GET", "api/search")]
        res = self.run_coro(fn(_Req({"q": "分机 8000", "n": "2"})))
        self.assertGreater(res["count"], 0)
        self.assertIn("8000", res["hits"][0]["text"])

    def test_api_reindex_force(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/reindex")]
        res = self.run_coro(fn(_Req(body={"force": True})))
        self.assertIn("added", res)

    def test_api_docs(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("GET", "api/docs")]
        res = self.run_coro(fn(_Req()))
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["documents"][0]["path"], "vpn.md")
        self.assertGreater(res["documents"][0]["chunks"], 0)

    def test_api_stats(self):
        plugin.setup(self.ctx)
        res = self.run_coro(self.ctx.apis[("GET", "api/stats")](_Req()))
        self.assertEqual(res["documents"], 1)

    # ── 自动建索引 ──
    def test_auto_index_when_empty(self):
        """索引为空时第一次搜索要自动建（否则新装的插件永远搜不到东西）。"""
        empty = Path(tempfile.mkdtemp(prefix="kb_empty_"))
        try:
            from gantry.kb import KnowledgeBase

            kb = KnowledgeBase(empty)
            plugin._kb = lambda ctx: kb
            calls = {"n": 0}
            orig = kb.reindex

            def counting(force=False):
                calls["n"] += 1
                return orig(force=force)

            kb.reindex = counting                     # type: ignore[assignment]
            ctx = FakeCtx(auto_reindex=True)
            plugin.setup(ctx)
            self.assertGreaterEqual(calls["n"], 1, "空索引应触发一次重建")
            kb.close()
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
