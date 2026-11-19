# -*- coding: utf-8 -*-
"""启动自动化浏览器接口必须立即返回，不能等 Copilot 就绪。"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gantry.api  # noqa: F401  （先初始化 api，避免 routes_config 单独导入时的循环导入）
from gantry import routes_config


class BrowserStartApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_returns_immediately_and_schedules_boot(self):
        boot = mock.AsyncMock()
        with mock.patch.object(routes_config.b, "current_page", return_value=None), \
             mock.patch.dict(routes_config.a.BOOT,
                             {"task": None, "browser_state": "idle"}, clear=False), \
             mock.patch.object(routes_config.a, "boot_browser", boot), \
             mock.patch.object(routes_config.a, "ensure_browser",
                               side_effect=AssertionError("must not wait for Copilot")):
            r = await routes_config.set_browser_mode({})
            await asyncio.sleep(0)
        self.assertTrue(r["started"])
        self.assertEqual(r["state"], "idle")
        boot.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
