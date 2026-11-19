# -*- coding: utf-8 -*-
"""自动化浏览器窗口尺寸：默认最小，并支持 config 覆盖/夹取。"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import bridge as b
from gantry import browser
from gantry.viewport import apply_outer_window_size, window_size


class WindowSizeTests(unittest.TestCase):
    def test_default_is_compact(self):
        with mock.patch.dict(b.CFG, {}, clear=False):
            b.CFG.pop("browser_width", None)
            b.CFG.pop("browser_height", None)
            kw = browser._launch_kwargs(False)
        self.assertEqual(kw["viewport"], {"width": 1024, "height": 768})

    def test_config_override(self):
        with mock.patch.dict(b.CFG, {"browser_width": 900, "browser_height": 700}):
            kw = browser._launch_kwargs(False)
        self.assertEqual(kw["viewport"], {"width": 900, "height": 700})

    def test_clamp(self):
        with mock.patch.dict(b.CFG, {"browser_width": 99999, "browser_height": 1}):
            kw = browser._launch_kwargs(False)
        self.assertEqual(kw["viewport"], {"width": 3200, "height": 600})
        with mock.patch.dict(b.CFG, {"browser_width": 1800, "browser_height": 9999}):
            kw = browser._launch_kwargs(False)
        self.assertEqual(kw["viewport"], {"width": 1800, "height": 1080})

    def test_login_uses_shared_size(self):
        src = (ROOT / "scripts" / "login.py").read_text(encoding="utf-8")
        self.assertIn("window_size(CFG)", src)
        self.assertNotIn("1440", src)
        self.assertEqual(window_size({"browser_width": 800, "browser_height": 600}),
                         {"width": 800, "height": 600})


class WindowBoundsTests(unittest.IsolatedAsyncioTestCase):
    async def test_cdp_sets_outer_size(self):
        page = mock.MagicMock()
        session = mock.MagicMock()
        session.send = mock.AsyncMock(side_effect=[{"windowId": 7}, {}])
        session.detach = mock.AsyncMock()
        page.context.new_cdp_session = mock.AsyncMock(return_value=session)
        page.evaluate = mock.AsyncMock(side_effect=[[1040, 902], [1024, 768]])
        page.set_viewport_size = mock.AsyncMock()
        ok = await apply_outer_window_size(page, {"browser_width": 1024,
                                                   "browser_height": 768})
        self.assertTrue(ok)
        session.send.assert_any_await("Browser.setWindowBounds", {
            "windowId": 7,
            "bounds": {"width": 1024, "height": 768, "windowState": "normal"},
        })
        page.set_viewport_size.assert_awaited_once_with({"width": 1008, "height": 634})

    async def test_cdp_failure_is_non_fatal(self):
        page = mock.MagicMock()
        page.context.new_cdp_session = mock.AsyncMock(side_effect=RuntimeError("headless"))
        self.assertFalse(await apply_outer_window_size(page, {}))


if __name__ == "__main__":
    unittest.main()
