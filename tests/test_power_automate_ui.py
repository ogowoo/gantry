# -*- coding: utf-8 -*-
"""Power Automate maker 门户探测：无浏览器时安全降级 + 路由存在。"""
import asyncio
import json
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
        self.assertIn('@router.post("/v1/power-automate/fill")', src)
        self.assertIn('@router.post("/v1/power-automate/press")', src)
        self.assertIn('@router.post("/v1/power-automate/import/open")', src)
        self.assertIn('@router.post("/v1/power-automate/import/run")', src)
        self.assertIn('@router.post("/v1/power-automate/frames")', src)
        self.assertIn('@router.post("/v1/power-automate/select")', src)
        self.assertIn('@router.post("/v1/power-automate/select-connection")', src)
        self.assertIn('@router.post("/v1/power-automate/html")', src)

    def test_fill_and_press_degrade_without_browser(self):
        old_ctx = getattr(b, "ctx", None)
        old_raw = getattr(b, "ENSURE_BROWSER_RAW", None)
        b.ctx = None
        b.ENSURE_BROWSER_RAW = None
        try:
            fill = asyncio.run(power_automate_ui.fill_text("Flow name", "x"))
            press = asyncio.run(power_automate_ui.press_key("Enter"))
        finally:
            b.ctx = old_ctx
            b.ENSURE_BROWSER_RAW = old_raw
        self.assertFalse(fill["ok"])
        self.assertFalse(press["ok"])
        self.assertIn("浏览器", fill["error"])
        self.assertIn("浏览器", press["error"])


class GotoPathAllowlistTests(unittest.TestCase):
    def test_nav_host_allowlist(self):
        allowed = power_automate_ui._nav_host_allowed
        self.assertTrue(allowed("https://make.powerautomate.com/flows"))
        self.assertTrue(allowed("https://make.powerapps.com/apps"))
        self.assertTrue(allowed("https://apps.powerapps.com/run?id=1"))
        self.assertTrue(allowed("https://flow.microsoft.com/manage"))
        self.assertTrue(allowed("https://login.microsoftonline.com/common/oauth2"))
        self.assertFalse(allowed("https://evil.example.com/"))
        self.assertFalse(allowed("https://make.powerautomate.com.evil.com/"))
        self.assertFalse(allowed("https://powerautomate.com.evil.com/"))
        self.assertFalse(allowed("https://login.microsoftonline.com.evil.com/"))
        self.assertFalse(allowed(""))

    def test_goto_path_rejects_offlist_absolute_url(self):
        class _Page:
            url = "about:blank"

            async def goto(self, *a, **k):  # pragma: no cover - 不该被调用
                raise AssertionError("绝不允许导航到白名单外 URL")

        class _Ctx:
            pages = []

            async def new_page(self):
                return _Page()

        old_ctx = getattr(b, "ctx", None)
        old_raw = getattr(b, "ENSURE_BROWSER_RAW", None)
        b.ENSURE_BROWSER_RAW = None
        b.ctx = _Ctx()
        try:
            out = asyncio.run(power_automate_ui.goto_path("https://evil.example.com/x"))
        finally:
            b.ctx = old_ctx
            b.ENSURE_BROWSER_RAW = old_raw
        self.assertFalse(out["ok"])
        self.assertIn("白名单", out["error"])

    def test_goto_path_rejects_subdomain_spoof(self):
        class _Page:
            url = "about:blank"

            async def goto(self, *a, **k):  # pragma: no cover - 不该被调用
                raise AssertionError("绝不允许导航到白名单外 URL")

        class _Ctx:
            pages = []

            async def new_page(self):
                return _Page()

        old_ctx = getattr(b, "ctx", None)
        old_raw = getattr(b, "ENSURE_BROWSER_RAW", None)
        b.ENSURE_BROWSER_RAW = None
        b.ctx = _Ctx()
        try:
            out = asyncio.run(
                power_automate_ui.goto_path("https://make.powerautomate.com.evil.com/x"))
        finally:
            b.ctx = old_ctx
            b.ENSURE_BROWSER_RAW = old_raw
        self.assertFalse(out["ok"])
        self.assertIn("白名单", out["error"])


class CookieDomainMatchTests(unittest.TestCase):
    def test_exact_or_suffix_match_only(self):
        ok = power_automate_ui._cookie_allowed
        self.assertTrue(ok("make.powerautomate.com"))
        self.assertTrue(ok(".make.powerautomate.com"))
        self.assertTrue(ok("login.microsoftonline.com"))
        self.assertTrue(ok(".login.microsoftonline.com"))
        self.assertTrue(ok("sub.apps.powerapps.com"))
        self.assertFalse(ok("notmicrosoft.com.evil.com"))
        self.assertFalse(ok("microsoft.com.evil.com"))
        self.assertFalse(ok("evilpowerapps.com"))
        self.assertFalse(ok("powerautomate.com.evil.com"))
        self.assertFalse(ok(""))
        self.assertFalse(ok("example.com"))


class SaveAuthStateTests(unittest.TestCase):
    def test_filters_spoofed_domain_and_sets_mode(self):
        import os
        import tempfile

        class _Ctx:
            async def storage_state(self):
                return {"cookies": [
                    {"name": "ok", "value": "v1", "domain": ".make.powerautomate.com"},
                    {"name": "spoof", "value": "v2", "domain": ".notmicrosoft.com.evil.com"},
                ]}

        old_base = b.BASE
        with tempfile.TemporaryDirectory() as td:
            b.BASE = Path(td)
            try:
                p = asyncio.run(power_automate_ui.save_auth_state(_Ctx()))
            finally:
                b.BASE = old_base
            self.assertTrue(p)
            data = json.loads(Path(p).read_text(encoding="utf-8"))
            names = [c["name"] for c in data["cookies"]]
            self.assertEqual(names, ["ok"])  # 欺骗域被过滤
            if os.name != "nt":
                self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
