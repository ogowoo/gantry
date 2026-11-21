# -*- coding: utf-8 -*-
"""Excel Data Guard：不信任 UsedRange，识别膨胀范围并安全读取。"""
import asyncio
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.excel_guard import inspect_workbook
from gantry.tools import ToolRegistry


def make_book(path: Path, rows: int = 500, cols: int = 10) -> None:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([f"col{i}" for i in range(1, cols + 1)])
    for r in range(2, rows + 1):
        ws.append([f"r{r}c{c}" for c in range(1, cols + 1)])
    wb.save(path)


def inflate_dimension(path: Path, ref: str = "A1:XFD1048576") -> None:
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet") and item.filename.endswith(".xml"):
                data = re.sub(br'<dimension ref="[^"]*"',
                              f'<dimension ref="{ref}"'.encode(), data, count=1)
            zout.writestr(item, data)
    tmp.replace(path)


class ExcelGuardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="excel_guard_"))
        self.tools = ToolRegistry(self.tmp, {"allow_office": True})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name: str, rows: int = 500, cols: int = 10,
              inflated: bool = True) -> Path:
        p = self.tmp / name
        make_book(p, rows, cols)
        if inflated:
            inflate_dimension(p)
        return p

    async def test_inflated_dimension_is_detected(self):
        p = self._make("bad.xlsx")
        report = await self.tools.excel_inspect("bad.xlsx", sheet="Data",
                                                max_cells=100000)
        self.assertTrue(report["ok"])
        sheet = report["sheets"][0]
        self.assertEqual(sheet["declared_range"], "A1:XFD1048576")
        self.assertEqual(sheet["detected_range"], "A1:J500")
        self.assertEqual(sheet["actual_rows"], 500)
        self.assertEqual(sheet["actual_columns"], 10)
        self.assertIn("used_range_inflated", sheet["anomalies"])
        self.assertGreater(sheet["confidence"], 0.7)

    async def test_read_excel_uses_detected_range(self):
        self._make("bad.xlsx")
        r = await self.tools.read_excel("bad.xlsx", sheet="Data", max_rows=5,
                                        max_cols=20)
        sheet = r["sheets"]["Data"]
        self.assertEqual(len(sheet["rows"]), 5)
        self.assertEqual(sheet["total_rows"], 500)
        self.assertEqual(sheet["total_columns"], 10)
        self.assertIn("used_range_inflated", sheet["anomalies"])
        self.assertIn("A1:J500", sheet["detected_range"])

    async def test_normal_book_not_flagged(self):
        p = self._make("good.xlsx", rows=20, cols=4, inflated=False)
        report = inspect_workbook(p, "Data")
        sheet = report["sheets"][0]
        self.assertEqual(sheet["detected_range"], "A1:D20")
        self.assertNotIn("used_range_inflated", sheet["anomalies"])

    async def test_empty_and_multi_sheet_reports(self):
        import openpyxl
        p = self.tmp / "multi.xlsx"
        wb = openpyxl.Workbook()
        wb.active.title = "Data"
        wb.active.append(["a", "b"])
        wb.create_sheet("Empty")
        wb.save(p)
        report = inspect_workbook(p)
        names = {s["sheet"]: s for s in report["sheets"]}
        self.assertIn("Data", names)
        self.assertIn("Empty", names)
        self.assertEqual(names["Empty"]["actual_rows"], 0)

    async def test_validate_detects_rows(self):
        self._make("bad.xlsx")
        r = await self.tools.excel_validate("bad.xlsx", sheet="Data", expect_rows=500)
        self.assertTrue(r["ok"])

    async def test_tool_schema(self):
        names = {x.get("name") for x in self.tools.descriptions()}
        self.assertIn("excel_inspect", names)
        self.assertIn("excel_validate", names)


if __name__ == "__main__":
    unittest.main()
