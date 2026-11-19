# -*- coding: utf-8 -*-
"""Excel 大文件快路径、read_excel 提前停止、事件循环不阻塞。"""
import asyncio
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.tools import ToolRegistry


class ExcelToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="excel_fast_"))
        self.tools = ToolRegistry(self.tmp, {"allow_office": True})
        self.src = (ROOT / "workspace" / "VBA" /
                    "20261118 DSV x DBS Customer Integration Tracker_all.xlsx")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_read_excel_stops_at_limit(self):
        if not self.src.is_file():
            self.skipTest("tracker fixture missing")
        dst = self.tmp / "tracker.xlsx"
        shutil.copyfile(self.src, dst)
        t0 = time.perf_counter()
        r = await self.tools.read_excel("tracker.xlsx",
                                        sheet="Customer Integration Tracker",
                                        max_rows=3)
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(r["sheets"]["Customer Integration Tracker"]["rows"]), 3)
        self.assertGreater(r["sheets"]["Customer Integration Tracker"]["total_rows"], 3)
        self.assertLess(elapsed, 10, f"read took {elapsed:.1f}s")

    async def test_large_write_uses_xml_fast_path_and_preserves_sheet(self):
        if not self.src.is_file():
            self.skipTest("tracker fixture missing")
        dst = self.tmp / "tracker.xlsx"
        shutil.copyfile(self.src, dst)
        import zipfile

        def _counts(p):
            with zipfile.ZipFile(p) as z:
                data = z.read("xl/worksheets/sheet2.xml").decode("utf-8", "ignore")
            return data.count("<conditionalFormatting"), data.count("<dataValidation ")
        before_counts = _counts(dst)
        r = await self.tools.write_excel(
            "tracker.xlsx", [["gantry-fast", 123]], "Customer Integration Tracker",
            "A5400")
        self.assertEqual(r["engine"], "xml-fast")
        self.assertEqual(_counts(dst), before_counts)
        import openpyxl
        wb = openpyxl.load_workbook(dst, data_only=False, read_only=True)
        try:
            ws = wb["Customer Integration Tracker"]
            row = next(ws.iter_rows(min_row=5400, max_row=5400,
                                    max_col=2, values_only=True))
            self.assertEqual(row, ("gantry-fast", 123))
        finally:
            wb.close()

    async def test_small_workbook_falls_back_to_openpyxl(self):
        import openpyxl
        dst = self.tmp / "small.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "old"
        wb.save(dst)
        r = await self.tools.write_excel("small.xlsx", [["new"]], "Sheet", "A2")
        self.assertEqual(r["engine"], "openpyxl")
        wb = openpyxl.load_workbook(dst)
        self.assertEqual(wb.active["A1"].value, "old")
        self.assertEqual(wb.active["A2"].value, "new")
        wb.close()


if __name__ == "__main__":
    unittest.main()
