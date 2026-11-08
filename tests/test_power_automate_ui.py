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
        old_fn, old_ctx = getattr(b, "ENSURE_BROWSER", None), getattr(b, "ctx", None)
        b.ENSURE_BROWSER = None
        b.ctx = None
        try:
            out = asyncio.run(power_automate_ui.probe())
        finally:
            b.ENSURE_BROWSER = old_fn
            b.ctx = old_ctx
        self.assertFalse(out["ok"])
        self.assertIn("浏览器未就绪", out["error"])

    def test_route_registered(self):
        src = (ROOT / "gantry" / "routes_core.py").read_text(encoding="utf-8")
        self.assertIn('@router.post("/v1/power-automate/probe")', src)
        self.assertIn('@router.post("/v1/power-automate/dom")', src)
        self.assertIn('@router.post("/v1/power-automate/login")', src)


if __name__ == "__main__":
    unittest.main()
