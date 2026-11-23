# -*- coding: utf-8 -*-
"""Power Apps maker 门户 UI：goto_path 绝对 URL 白名单（M12）。"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_apps_ui as ui  # noqa: E402
from gantry import bridge as b  # noqa: E402


class _OfflistPage:
    """记录导航调用的页面桩；若被调用说明白名单拦截失效。"""

    url = "about:blank"
    navigated = None

    async def goto(self, *a, **k):
        type(self).navigated = a[0] if a else None
        raise AssertionError("不应导航到白名单外 URL")


class _Ctx:
    pages = []

    async def new_page(self):
        return _OfflistPage()


class GotoPathTests(unittest.TestCase):
    def _run(self, path: str) -> dict:
        old_ctx = getattr(b, "ctx", None)
        old_raw = getattr(b, "ENSURE_BROWSER_RAW", None)
        b.ENSURE_BROWSER_RAW = None
        b.ctx = _Ctx()
        try:
            return asyncio.run(ui.goto_path(path))
        finally:
            b.ctx = old_ctx
            b.ENSURE_BROWSER_RAW = old_raw

    def test_rejects_arbitrary_absolute_url(self):
        out = self._run("https://evil.example.com/x")
        self.assertFalse(out["ok"])
        self.assertIn("白名单", out["error"])

    def test_rejects_subdomain_spoof(self):
        # make.powerapps.com.evil.com 不得因子串相似而放行
        out = self._run("https://make.powerapps.com.evil.com/x")
        self.assertFalse(out["ok"])
        self.assertIn("白名单", out["error"])

    def test_shares_core_allowlist(self):
        # power_apps_ui 复用 power_automate_ui 的白名单（单一来源）
        self.assertIs(ui.core._nav_host_allowed,
                      __import__("gantry.power_automate_ui", fromlist=["x"])._nav_host_allowed)
        allowed = ui.core._nav_host_allowed
        self.assertTrue(allowed("https://make.powerapps.com/apps"))
        self.assertTrue(allowed("https://login.microsoftonline.com/common/oauth2"))
        self.assertFalse(allowed("https://evil.example.com/"))


if __name__ == "__main__":
    unittest.main()
