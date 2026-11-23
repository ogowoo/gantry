# -*- coding: utf-8 -*-
"""Excel 子进程超时/内存保护的最小验证。"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.tools import ToolError, ToolRegistry


class ExcelWorkerLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="excel_worker_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _book(self):
        import openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["a", "b"])
        wb.active.append(["1", "2"])
        wb.save(self.tmp / "x.xlsx")

    async def test_timeout_kills_worker(self):
        self._book()
        tools = ToolRegistry(self.tmp, {"allow_office": True,
                                       "excel_read_timeout_seconds": 0.001})
        with self.assertRaises(ToolError):
            await tools.read_excel("x.xlsx", max_rows=2)

    async def test_xls_is_explicitly_rejected(self):
        (self.tmp / "old.xls").write_bytes(b"not an xlsx")
        tools = ToolRegistry(self.tmp, {"allow_office": True})
        with self.assertRaises(ToolError) as ctx:
            await tools.read_excel("old.xls")
        # 断言工具自身抛出的 ToolError 文案，而不是 openpyxl 的报错文本：
        # 无 calamine 时走 _reject_legacy_xls；有 calamine 时走 worker 的
        # "无法读取旧版 .xls 文件"。两条路径都必须是我们自己的文案。
        msg = str(ctx.exception)
        self.assertIn("旧版", msg)
        self.assertNotIn("InvalidFileException", msg)


class WorkerRestartTests(unittest.TestCase):
    """_WorkerDead 触发重启重试：死一次恢复，死两次转 ToolError。"""

    def _pool(self):
        from gantry import tools_office
        return tools_office._PersistentExcelWorker()

    def test_dead_once_restarts_and_retries(self):
        from gantry import tools_office
        pool = self._pool()
        calls = {"n": 0}

        def fake_once(payload, timeout, limit_mb, root):
            calls["n"] += 1
            if calls["n"] == 1:
                raise tools_office._WorkerDead("boom")
            return {"ok": True, "result": {"rows": [[1]]}}

        pool._request_once = fake_once
        try:
            out = pool.request({}, 10.0, 512, Path("."))
        finally:
            pool._stop()
        self.assertEqual(calls["n"], 2)
        self.assertEqual(out["result"], {"rows": [[1]]})

    def test_dead_twice_raises_tool_error(self):
        from gantry import tools_office
        pool = self._pool()
        calls = {"n": 0}

        def fake_once(payload, timeout, limit_mb, root):
            calls["n"] += 1
            raise tools_office._WorkerDead(f"boom {calls['n']}")

        pool._request_once = fake_once
        try:
            with self.assertRaises(ToolError) as ctx:
                pool.request({}, 10.0, 512, Path("."))
        finally:
            pool._stop()
        self.assertEqual(calls["n"], 2)  # 只重试一次
        self.assertIn("反复崩溃", str(ctx.exception))


class ServeProtocolTests(unittest.TestCase):
    """serve() 行协议：坏 JSON 给错误响应；EOF 半行不崩溃。"""

    def _run_serve(self, raw: bytes):
        import io
        import json as _json
        from gantry import excel_worker
        fake_stdin = type("S", (), {"buffer": io.BytesIO(raw)})()
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin, buf = fake_stdin, io.StringIO()
        sys.stdout = buf
        try:
            rc = excel_worker.serve()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        return rc, [_json.loads(l) for l in lines]

    def test_bad_json_gets_error_response(self):
        rc, msgs = self._run_serve(b"this is not json\n")
        self.assertEqual(rc, 0)
        self.assertEqual(len(msgs), 1)
        self.assertFalse(msgs[0]["ok"])
        self.assertIn("error", msgs[0])

    def test_half_line_at_eof_is_handled(self):
        # EOF 处没有换行的半行：不崩溃，按一条请求处理（未知 action → 错误响应）
        payload = '{"action": "unknown_op"}'  # 无结尾换行
        rc, msgs = self._run_serve(payload.encode("utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(len(msgs), 1)
        self.assertFalse(msgs[0]["ok"])

    def test_utf8_bytes_roundtrip(self):
        # 父进程写 UTF-8 字节：中文必须原样往返，不依赖 locale
        payload = '{"action": "unknown_op", "备注": "中文路径"}'.encode("utf-8") + b"\n"
        rc, msgs = self._run_serve(payload)
        self.assertEqual(rc, 0)
        self.assertEqual(len(msgs), 1)
        self.assertFalse(msgs[0]["ok"])
        self.assertIn("unknown_op", msgs[0]["error"])


if __name__ == "__main__":
    unittest.main()
