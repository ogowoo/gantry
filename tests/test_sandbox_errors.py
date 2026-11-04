"""沙箱拒绝必须结构化且可行动（DSH 的 FS_SANDBOX_DENIED 思路）。"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.tools import ToolError, ToolRegistry


class SandboxErrorTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_sbx_"))
        self.tools = ToolRegistry(self.ws, {})

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _denied(self, tool, args):
        with self.assertRaises(ToolError) as ctx:
            asyncio.run(self.tools.execute(tool, args))
        return str(ctx.exception)

    def test_escape_denied_is_structured(self):
        msg = self._denied("read_text", {"path": "../../etc/passwd"})
        self.assertIn("FS_SANDBOX_ESCAPE", msg)
        self.assertIn("当前模式", msg)
        self.assertIn("workspace-write", msg)
        self.assertIn("原因:", msg)
        self.assertIn("可以这样做", msg)
        self.assertIn("allow_self_upgrade", msg, "要告诉模型合法的升级途径")

    def test_write_escape_also_structured(self):
        msg = self._denied("write_text", {"path": "../outside.txt", "content": "x"})
        self.assertIn("FS_SANDBOX_ESCAPE", msg)

    def test_inside_workspace_still_allowed(self):
        (self.ws / "ok.txt").write_text("hi", encoding="utf-8")
        out = asyncio.run(self.tools.execute("read_text", {"path": "./ok.txt"}))
        self.assertIn("hi", str(out))

    def test_no_denylist_leak_of_absolute_agent_path(self):
        """拒绝信息里不要泄露服务安装目录的绝对路径细节（只给模式与建议）。"""
        msg = self._denied("read_text", {"path": "../x"})
        self.assertNotIn("EdgeProfile", msg)


class SandboxPathHintTests(unittest.TestCase):
    """越界路径 → 工作区相对路径的建议（现场：/src/minesweeper.py）。"""

    def setUp(self):
        import tempfile
        from gantry.tools import ToolRegistry

        self.ws = Path(tempfile.mkdtemp(prefix="cbp_hint_"))
        self.tools = ToolRegistry(self.ws, {})

    def tearDown(self):
        import shutil

        shutil.rmtree(self.ws, ignore_errors=True)

    def test_posix_absolute_gets_relative_suggestion(self):
        msg = self.tools._sandbox_denied("FS_SANDBOX_ESCAPE", "/src/minesweeper.py", "越界")
        self.assertIn('path="src/minesweeper.py"', msg)
        self.assertIn(str(self.ws.resolve()), msg, "还要给出最终落盘位置")

    def test_windows_drive_path(self):
        msg = self.tools._sandbox_denied("FS_SANDBOX_ESCAPE", r"C:\tmp\a.py", "越界")
        self.assertIn('path="tmp/a.py"', msg)

    def test_parent_traversal(self):
        msg = self.tools._sandbox_denied("FS_SANDBOX_ESCAPE", r"..\..\evil.py", "越界")
        self.assertIn('path="evil.py"', msg)

    def test_hint_helper(self):
        hint = self.tools._workspace_relative_hint
        self.assertEqual(hint("/src/minesweeper.py"), "src/minesweeper.py")
        self.assertEqual(hint(r"C:\Users\x\proj\a.py"), "x/proj/a.py")
        self.assertEqual(hint("/"), "")
        self.assertEqual(hint(""), "")

    def test_no_suggestion_for_unrelated_codes(self):
        msg = self.tools._sandbox_denied("FS_SANDBOX_DENYLIST", "config.json", "敏感文件")
        self.assertNotIn('path="', msg)


class B64PayloadGuardTests(unittest.TestCase):
    """现场（task-mw1cb7ue）：模型给的 content_b64 能解码，但内容是乱码拼接。"""

    def test_normal_code_passes(self):
        from gantry.tools_files import _guard_printable

        _guard_printable('print("在 你好")\n# 注释\nx = 1\n')

    def test_tabs_and_newlines_pass(self):
        from gantry.tools_files import _guard_printable

        _guard_printable("a\tb\r\nc\n" * 20)

    def test_garbage_control_chars_rejected(self):
        from gantry.tools import ToolError
        from gantry.tools_files import _guard_printable

        with self.assertRaises(ToolError) as ctx:
            _guard_printable("abc" + "\x00\x01\x02" * 7 + "def")
        msg = str(ctx.exception)
        self.assertIn("不像文本", msg)
        self.assertIn("write_text", msg, "要告诉模型改用 write_text")

    def test_b64_tool_rejects_garbage(self):
        import asyncio
        import base64
        import tempfile
        from gantry.tools import ToolError, ToolRegistry

        ws = Path(tempfile.mkdtemp(prefix="cbp_b64_"))
        tools = ToolRegistry(ws, {})
        payload = base64.b64encode(b"\x00\x01" * 40).decode()
        try:
            with self.assertRaises(ToolError) as ctx:
                asyncio.run(tools.execute("write_text_b64",
                                          {"path": "x.py", "content_b64": payload}))
            self.assertIn("不像文本", str(ctx.exception))
        finally:
            import shutil

            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
