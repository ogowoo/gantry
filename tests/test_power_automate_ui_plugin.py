# -*- coding: utf-8 -*-
"""power-automate UI 插件：默认关闭、可配置、快照裁剪。"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_automate_ui as ui
from gantry.tools import ToolError

PLUGIN = ROOT / "plugins" / "power-automate" / "plugin.py"


def _load():
    spec = importlib.util.spec_from_file_location("pa_ui_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    def __init__(self):
        self.config = {"enabled": False, "dry_run": True, "max_items": 3}
        self.state = {}
        self.tools = {}
        self.apis = {}
        self.host = SimpleNamespace(registry=SimpleNamespace(
            _path=lambda p: (ROOT / p).resolve()))

    def register_tool(self, name, fn, description, arguments, approval=False):
        self.tools[name] = fn

    def register_api(self, method, path, fn):
        self.apis[(method, path)] = fn

    def save_state(self, **kw):
        self.state.update(kw)


class PowerAutomateUiPluginTests(unittest.TestCase):
    def test_disabled_by_default_and_settings_toggle(self):
        mod = _load()
        ctx = FakeCtx()
        mod.setup(ctx)
        self.assertEqual(set(ctx.tools), set(mod._TOOL_NAMES))
        with self.assertRaises(ToolError):
            asyncio.run(ctx.tools["pa_snapshot"]())

        class Req:
            async def json(self):
                return {"enabled": True, "max_items": 2}

        asyncio.run(ctx.apis[("POST", "api/settings")](Req()))
        self.assertTrue(ctx.state["overrides"]["enabled"])
        out = asyncio.run(ctx.apis[("GET", "api/status")](None))
        self.assertTrue(out["enabled"])
        self.assertEqual(out["max_items"], 2)

    def test_snapshot_is_trimmed_and_filtered(self):
        mod = _load()
        ctx = FakeCtx()
        ctx.config["enabled"] = True
        ctx.config["max_items"] = 2
        mod.setup(ctx)

        async def fake_snapshot():
            return {"ok": True, "url": "x", "items": [
                {"text": "Create"}, {"text": "My flows"},
                {"text": "Create flow"}, {"text": "Other"}]}

        old = ui.dom_snapshot
        ui.dom_snapshot = fake_snapshot
        try:
            out = asyncio.run(ctx.tools["pa_snapshot"](query="create"))
        finally:
            ui.dom_snapshot = old
        self.assertEqual([x["text"] for x in out["items"]], ["Create", "Create flow"])
        self.assertEqual(out["item_total"], 2)


if __name__ == "__main__":
    unittest.main()
