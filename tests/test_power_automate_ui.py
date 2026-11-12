# -*- coding: utf-8 -*-
"""Power Automate maker 门户探测：无浏览器时安全降级 + 路由存在。"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_automate_ui  # noqa: E402
from gantry import bridge as b  # noqa: E402


class ProbeTests(unittest.TestCase):
    def test_probe_without_browser_returns_error(self):
        old_fn = getattr(b, "ENSURE_BROWSER", None)
        old_raw = getattr(b, "ENSURE_BROWSER_RAW", None)
        old_ctx = getattr(b, "ctx", None)
        b.ENSURE_BROWSER = None
        b.ENSURE_BROWSER_RAW = None
        b.ctx = None
        try:
            out = asyncio.run(power_automate_ui.probe())
        finally:
            b.ENSURE_BROWSER = old_fn
            b.ENSURE_BROWSER_RAW = old_raw
            b.ctx = old_ctx
        self.assertFalse(out["ok"])
        self.assertIn("浏览器未就绪", out["error"])

    def test_route_registered(self):
        src = (ROOT / "gantry" / "routes_core.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/v1/power-automate/probe")', src)
        self.assertIn('@router.post("/v1/power-automate/dom")', src)
        self.assertIn('@router.post("/v1/power-automate/login")', src)
        self.assertIn('@router.post("/v1/power-automate/goto")', src)
        self.assertIn('@router.post("/v1/power-automate/click")', src)
        self.assertIn('@router.post("/v1/power-automate/import/open")', src)
        self.assertIn('@router.post("/v1/power-automate/import/run")', src)
        self.assertIn('@router.post("/v1/power-automate/frames")', src)
        self.assertIn('@router.post("/v1/power-automate/select")', src)
        self.assertIn('@router.post("/v1/power-automate/select-connection")', src)
        self.assertIn('@router.post("/v1/power-automate/html")', src)

    def test_fill_and_press_degrade_without_browser(self):
        old_ctx = getattr(b, "ctx", None)
        b.ctx = None
        try:
            fill = asyncio.run(power_automate_ui.fill_text("Flow name", "x"))
            press = asyncio.run(power_automate_ui.press_key("Enter"))
        finally:
            b.ctx = old_ctx
        self.assertFalse(fill["ok"])
        self.assertFalse(press["ok"])
        self.assertIn("浏览器", fill["error"])
        self.assertIn("浏览器", press["error"])


if __name__ == "__main__":
    unittest.main()
