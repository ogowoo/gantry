# -*- coding: utf-8 -*-
"""Windows 子进程不应弹出前台控制台窗口。"""
import unittest
from pathlib import Path

from gantry.subproc import hidden_kwargs

ROOT = Path(__file__).resolve().parent.parent


class HiddenSubprocessTests(unittest.TestCase):
    def test_hidden_kwargs_are_platform_safe(self):
        kw = hidden_kwargs()
        if __import__("os").name == "nt":
            self.assertIn("creationflags", kw)
            self.assertTrue(kw["creationflags"])
        else:
            self.assertEqual(kw, {})

    def test_agent_python_tools_hide_windows(self):
        for name in ("tools_exec.py", "tools_self.py", "run_code.py", "updater.py"):
            src = (ROOT / "gantry" / name).read_text(encoding="utf-8")
            self.assertIn("hidden_kwargs", src, f"{name} 没有使用隐藏窗口参数")
        for fn, msg in (("run_unittest", "测试"), ("run_python", "Python")):
            src = (ROOT / "gantry" / "tools_exec.py").read_text(encoding="utf-8")
            start = src.index(f"async def {fn}(")
            try:
                end = src.index("\n    async def ", start + 5)
            except ValueError:
                end = len(src)
            seg = src[start:end]
            self.assertIn("**hidden_kwargs()", seg, f"{msg} 子进程仍会弹窗")


if __name__ == "__main__":
    unittest.main()
