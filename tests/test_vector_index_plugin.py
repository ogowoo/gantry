# -*- coding: utf-8 -*-
"""vector-index 插件：manifest 默认关闭、索引/检索、管理面板与 API 注册。"""
import importlib.util
import json
import math
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "vector_index_plugin", ROOT / "plugins" / "vector-index" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def _fake_embed(cfg, texts):
    out = []
    for text in texts:
        t = str(text)
        vec = [0.0] * int(cfg["dim"])
        if "违约" in t or "赔偿" in t:
            vec[0] = 1.0
        elif "打印" in t:
            vec[1] = 1.0
        else:
            vec[2] = 1.0
        n = math.sqrt(sum(x * x for x in vec)) or 1.0
        out.append([x / n for x in vec])
    return out


class FakeHost:
    def __init__(self, root):
        self.root = root


class FakeCtx:
    def __init__(self, root):
        self.host = FakeHost(root)
        self.config = {"base_url": "http://fake/v1", "model": "BAAI/bge-m3", "dim": 8}
        self.state = {}
        self.tools = {}
        self.apis = {}
        self.panels = []

    def log(self, msg, *a, **k):
        pass

    def save_state(self, **kw):
        self.state.update(kw)

    def register_tool(self, name, fn, desc, args=None):
        self.tools[name] = fn

    def register_api(self, method, path, fn):
        self.apis[(method, path)] = fn

    def register_panel(self, panel):
        self.panels.append(panel)


DOC = """---
title: 合同违约责任
law: 民法典
article: 第五百八十四条
---

# 合同

## 违约责任

违约方应当赔偿损失。赔偿范围包括实际损失和可得利益。
"""


class VectorIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vi_test_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        (kbdir / "c.md").write_text(DOC, encoding="utf-8")
        from gantry.kb import KnowledgeBase
        kb = KnowledgeBase(self.tmp)
        kb.reindex()
        kb.close()
        self.ctx = FakeCtx(self.tmp)

    def tearDown(self):
        vi = getattr(self.ctx, "_vector_index", None)
        if vi is not None:
            vi.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_and_search(self):
        with mock.patch.object(plugin, "_embed", side_effect=_fake_embed):
            vi = plugin.VectorIndex(self.ctx)
            st = vi.build()
            self.assertIn("indexed", st)
            self.assertEqual(st["backend"], "numpy")
            hits = vi.search("违约赔偿", 2)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["title"], "合同违约责任")
        self.assertIn("违约", hits[0]["text"])
        self.assertIn("meta", hits[0])

    def test_reset_does_not_touch_kb(self):
        with mock.patch.object(plugin, "_embed", side_effect=_fake_embed):
            vi = plugin.VectorIndex(self.ctx)
            vi.build()
            vi.reset()
            self.assertEqual(vi.status()["indexed"], 0)
        self.assertTrue((self.tmp / "data" / "kb" / "c.md").is_file())

    def test_setup_registers_admin_panel_and_apis(self):
        plugin.setup(self.ctx)
        self.assertIn("vector_index_status", self.ctx.tools)
        self.assertIn("vector_index_reindex", self.ctx.tools)
        self.assertIn("vector_index_search", self.ctx.tools)
        for path in ("api/admin", "api/config", "api/reindex", "api/search",
                     "api/health"):
            self.assertIn(("GET", path) if path in ("api/admin", "api/search", "api/health")
                          else ("POST", path), self.ctx.apis)
        self.assertTrue(self.ctx.panels)
        p = self.ctx.panels[0]
        self.assertEqual(p["view"], "admin")
        self.assertTrue(p["fields"])
        self.assertTrue(any(a["id"] == "reindex" for a in p["actions"]))
        self.assertTrue(p["search"])

    def test_manifest_default_disabled(self):
        from gantry.plugins import _read_manifest
        m = _read_manifest(ROOT / "plugins" / "vector-index")
        self.assertIsNotNone(m)
        self.assertFalse(m.default_enabled)
        self.assertEqual(m.name, "vector-index")

    def test_real_plugin_host_accepts_admin_panel(self):
        from gantry.plugins import PluginHost
        from gantry.tools import ToolRegistry
        host = PluginHost(ROOT, registry=ToolRegistry(self.tmp / "ws", {}))
        try:
            host.load("vector-index")
            panel = next(p for p in host.panels() if p["id"] == "vector-index")
            self.assertEqual(panel["view"], "admin")
            self.assertEqual(panel["path"], "api/admin")
            self.assertTrue(any(a["id"] == "reindex" for a in panel["actions"]))
            self.assertIn("vector_index_search", host.registry._tools)
        finally:
            host.unload("vector-index")


if __name__ == "__main__":
    unittest.main()
