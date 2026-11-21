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
        self.assertIn(".xls", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
