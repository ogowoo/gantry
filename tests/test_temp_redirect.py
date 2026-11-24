# -*- coding: utf-8 -*-
"""gantry/temphard.py：系统 Temp 拒写（EPERM）时改指项目内 data\\tmp。

公司策略/沙箱/EDR 会拒写 %TEMP%，Playwright 起浏览器 mkdtemp 直接 EPERM
（浏览器永远起不来、提供商模型不受影响，症状有迷惑性）。Playwright 的 node
驱动只认 %TEMP% 无回落，而 Python tempfile 探测失败会静默回落 cwd——所以
探测必须针对 node 将要使用的目录本身（见 temphard.py docstring）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.temphard import ensure_writable_temp


class TempRedirectTests(unittest.TestCase):
    def test_redirect_when_system_temp_unwritable(self):
        saved = {k: os.environ.get(k) for k in ("TEMP", "TMP", "TMPDIR")}
        tempfile.tempdir = None
        # 模拟系统 Temp 不可写：指向不存在的目录，探测（mkdir）必然失败
        for k in saved:
            os.environ[k] = r"C:\__gantry_no_such__\temp"
        try:
            tmp = ensure_writable_temp(ROOT)
            self.assertIsNotNone(tmp)
            self.assertEqual(tmp, Path(ROOT) / "data" / "tmp")
            self.assertEqual(os.environ["TEMP"], str(tmp))
            self.assertEqual(os.environ["TMP"], str(tmp))
            self.assertEqual(Path(tempfile.gettempdir()), tmp)
            self.assertTrue(tmp.is_dir())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            tempfile.tempdir = None

    def test_noop_when_system_temp_writable(self):
        tmp = ensure_writable_temp(ROOT)
        self.assertIsNone(tmp, "Temp 可写时必须零行为变化")
        self.assertNotEqual(os.environ.get("TEMP"), str(Path(ROOT) / "data" / "tmp"))


if __name__ == "__main__":
    unittest.main()
