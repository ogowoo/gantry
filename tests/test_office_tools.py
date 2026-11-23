# -*- coding: utf-8 -*-
"""Office 工具补漏测试：CSV 读写、pptx 表格提取、calamine 引擎、探测缓存。"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.tools import ToolRegistry


def make_registry(root: Path) -> ToolRegistry:
    return ToolRegistry(root=root, config={})


class CsvTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_csv_"))
        self.tools = make_registry(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_write_then_read_roundtrip(self):
        rows = [["名称", "数量"], ["苹果", 3], ["香蕉,带逗号", 2]]
        w = await self.tools.write_csv("t.csv", rows)
        self.assertEqual(w["rows_written"], 3)
        r = await self.tools.read_csv("t.csv")
        self.assertEqual(r["rows"][0], ["名称", "数量"])
        self.assertEqual(r["rows"][2], ["香蕉,带逗号", "2"])
        self.assertFalse(r["truncated"])

    async def test_append_mode(self):
        await self.tools.write_csv("t.csv", [["a", 1]])
        await self.tools.write_csv("t.csv", [["b", 2]], mode="append")
        r = await self.tools.read_csv("t.csv")
        self.assertEqual(len(r["rows"]), 2)

    async def test_gbk_encoding_fallback(self):
        p = self.tmp / "gbk.csv"
        p.write_bytes("姓名,城市\n张三,北京\n".encode("gbk"))
        r = await self.tools.read_csv("gbk.csv")
        self.assertEqual(r["encoding"], "gb18030")
        self.assertIn(["张三", "北京"], r["rows"])

    async def test_truncation(self):
        await self.tools.write_csv("big.csv", [[i] for i in range(100)])
        r = await self.tools.read_csv("big.csv", max_rows=10)
        self.assertTrue(r["truncated"])
        self.assertEqual(r["rows_read"], 10)

    async def test_binary_content_rejected_not_mojibake(self):
        # 二进制内容会被 gb18030 "成功"解码成乱码；启发校验必须拦住并报 ToolError
        p = self.tmp / "bin.csv"
        p.write_bytes(b"\x00\x01\x02\x03name\x00city\n")
        with self.assertRaises(Exception) as ctx:
            await self.tools.read_csv("bin.csv")
        self.assertIn("无法解码", str(ctx.exception))

    async def test_escape_formulas_prefixes_quote(self):
        await self.tools.write_csv("f.csv", [["=cmd", "+x", "-y", "@z", "plain", 5]])
        r = await self.tools.read_csv("f.csv")
        self.assertEqual(r["rows"][0][:4], ["'=cmd", "'+x", "'-y", "'@z"])
        self.assertEqual(r["rows"][0][4:], ["plain", "5"])

    async def test_escape_formulas_can_be_disabled(self):
        await self.tools.write_csv("f2.csv", [["=1+1"]], escape_formulas=False)
        r = await self.tools.read_csv("f2.csv")
        self.assertEqual(r["rows"][0], ["=1+1"])

    async def test_write_csv_atomic_and_append(self):
        await self.tools.write_csv("a.csv", [["x"]])
        await self.tools.write_csv("a.csv", [["y"]], mode="append")
        r = await self.tools.read_csv("a.csv")
        self.assertEqual([row[0] for row in r["rows"]], ["x", "y"])
        # 原子写：失败时不会留下半个文件
        self.assertFalse((self.tmp / "a.csv.tmp").exists())


class PptxTableTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="office_pptx_"))
        self.tools = make_registry(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_table_and_grouped_shape_are_extracted(self):
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        tbl = slide.shapes.add_table(2, 2, Inches(1), Inches(1),
                                     Inches(4), Inches(1)).table
        tbl.cell(0, 0).text = "表头A"
        tbl.cell(0, 1).text = "表头B"
        tbl.cell(1, 0).text = "值1"
        tbl.cell(1, 1).text = "值2"
        slide.shapes.title.text = "标题文本"
        prs.save(self.tmp / "t.pptx")

        r = await self.tools.read_pptx("t.pptx")
        slide0 = r["slides"][0]
        self.assertIn("表头A | 表头B", slide0["tables"])
        self.assertIn("值1 | 值2", slide0["tables"])
        self.assertIn("标题文本", slide0["text"])


class CalamineEngineTests(unittest.TestCase):
    """calamine 引擎的输出须与 openpyxl 语义一致（数字整型化、布尔、空值）。"""
    def setUp(self):
        import python_calamine  # noqa: F401 — 无 calamine 时整类跳过
        self.tmp = Path(tempfile.mkdtemp(prefix="office_cal_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cell_str_matches_openpyxl_semantics(self):
        from gantry.excel_worker import _cell_str
        self.assertEqual(_cell_str(None), "")
        self.assertEqual(_cell_str(2.0), "2")          # calamine 2.0 → openpyxl 的 "2"
        self.assertEqual(_cell_str(3.14), "3.14")
        self.assertEqual(_cell_str(True), "True")
        self.assertEqual(_cell_str("abc"), "abc")
        import datetime
        self.assertEqual(_cell_str(datetime.datetime(2024, 1, 1)),
                         str(datetime.datetime(2024, 1, 1)))

    def test_read_workbook_calamine_parity(self):
        import openpyxl
        from gantry.excel_worker import _read_workbook, _read_range
        p = self.tmp / "t.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "S"
        ws.append(["id", "名称", "比率", "开启"])
        ws.append([1, "甲", 0.5, True])
        ws.append([2, "乙", None, False])
        wb.save(p)

        out = _read_workbook({"path": str(p), "sheet": "", "limit": 200,
                              "columns": 300, "region": 0, "report": {}})
        rows = out["sheets"]["S"]["rows"]
        self.assertEqual(rows[0], ["id", "名称", "比率", "开启"])
        self.assertEqual(rows[1], ["1", "甲", "0.5", "True"])
        self.assertEqual(rows[2], ["2", "乙", "", "False"])

        rr = _read_range({"path": str(p), "sheet": "S", "start_row": 2,
                          "end_row": 3, "max_col": 2})
        self.assertEqual(rr, [["1", "甲"], ["2", "乙"]])


class InspectCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_call_hits_cache(self):
        import openpyxl
        from unittest.mock import AsyncMock
        tmp = Path(tempfile.mkdtemp(prefix="office_cache_"))
        try:
            wb = openpyxl.Workbook()
            wb.active.append(["a"])
            wb.save(tmp / "t.xlsx")
            tools = make_registry(tmp)
            tools._excel_worker = AsyncMock(
                return_value={"ok": True, "sheets": []})
            t = tmp / "t.xlsx"
            await tools._inspect_report(t, "", 200_000, 200_000, 8.0)
            await tools._inspect_report(t, "", 200_000, 200_000, 8.0)
            self.assertEqual(tools._excel_worker.call_count, 1)
            # 文件被改写 → 缓存键变化 → 重新探测
            wb2 = openpyxl.Workbook()
            wb2.active.append(["b"])
            wb2.save(tmp / "t.xlsx")
            await tools._inspect_report(t, "", 200_000, 200_000, 8.0)
            self.assertEqual(tools._excel_worker.call_count, 2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
