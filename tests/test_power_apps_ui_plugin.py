# -*- coding: utf-8 -*-
"""power-apps UI 插件与基础控件：默认关闭、可配置、无浏览器降级。"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import bridge as b
from gantry import power_apps_ui as ui
from gantry.tools import ToolError

PLUGIN = ROOT / "plugins" / "power-apps" / "plugin.py"


def _load():
    spec = importlib.util.spec_from_file_location("papp_ui_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    def __init__(self):
        self.config = {"enabled": False, "max_items": 2}
        self.state = {}
        self.tools = {}
        self.apis = {}
        self.host = SimpleNamespace(registry=SimpleNamespace())

    def register_tool(self, name, fn, description, arguments, approval=False):
        self.tools[name] = fn

    def register_api(self, method, path, fn):
        self.apis[(method, path)] = fn

    def save_state(self, **kw):
        self.state.update(kw)


class PowerAppsUiTests(unittest.TestCase):
    def test_without_browser_degrades(self):
        old = getattr(b, "ctx", None)
        b.ctx = None
        try:
            probe = asyncio.run(ui.probe())
            fill = asyncio.run(ui.fill_text("App name", "x"))
            press = asyncio.run(ui.press_key("Enter"))
        finally:
            b.ctx = old
        self.assertFalse(probe["ok"])
        self.assertFalse(fill["ok"])
        self.assertFalse(press["ok"])

    def test_plugin_disabled_and_settings(self):
        mod = _load()
        ctx = FakeCtx()
        mod.setup(ctx)
        self.assertEqual(set(ctx.tools), set(mod._TOOL_NAMES))
        with self.assertRaises(ToolError):
            asyncio.run(ctx.tools["papp_snapshot"]())

        class Req:
            async def json(self):
                return {"enabled": True, "max_items": 1}

        asyncio.run(ctx.apis[("POST", "api/settings")](Req()))
        out = asyncio.run(ctx.apis[("GET", "api/status")](None))
        self.assertTrue(out["enabled"])
        self.assertEqual(out["max_items"], 1)

    def test_snapshot_trimmed(self):
        mod = _load()
        ctx = FakeCtx()
        ctx.config["enabled"] = True
        mod.setup(ctx)

        async def fake_snapshot():
            return {"ok": True, "items": [{"text": "Apps"}, {"text": "Create"},
                                          {"text": "Solutions"}]}

        old = ui.dom_snapshot
        ui.dom_snapshot = fake_snapshot
        try:
            out = asyncio.run(ctx.tools["papp_snapshot"](query="create"))
        finally:
            ui.dom_snapshot = old
        self.assertEqual([x["text"] for x in out["items"]], ["Create"])
        self.assertEqual(out["item_total"], 1)


if __name__ == "__main__":
    unittest.main()
