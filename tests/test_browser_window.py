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


class WindowSizeTests(unittest.TestCase):
    def test_default_is_minimum(self):
        with mock.patch.dict(b.CFG, {}, clear=False):
            b.CFG.pop("browser_width", None)
            b.CFG.pop("browser_height", None)
            kw = browser._launch_kwargs(False)
        self.assertEqual(kw["viewport"], {"width": 800, "height": 600})

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


if __name__ == "__main__":
    unittest.main()
