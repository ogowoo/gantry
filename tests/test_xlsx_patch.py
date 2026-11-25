"""xlsx_patch.write_cells 回归：写入正确 + 未修改的 zip 条目保留原元数据。

对应审查报告 M22：旧实现 zout.writestr(item.filename, data) 只传文件名，
条目时间戳/压缩方式全部丢成默认值（Excel 打开即提示"包被修改"，diff 全红）。
"""
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.xlsx_patch import write_cells


def _make_xlsx(path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "hello"
    wb.save(str(path))


def _make_xlsx_with_self_closing(path: Path):
    """造一个含自闭合 <row/> 的 sheet：<row r="5" hidden="1"/> 后紧跟完整 <row r="6">。"""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "hello"
    wb.save(str(path))
    with zipfile.ZipFile(path) as zin:
        items = {i.filename: zin.read(i.filename) for i in zin.infolist()}
    xml = items["xl/worksheets/sheet1.xml"].decode("utf-8")
    xml = xml.replace(
        "</sheetData>",
        '<row r="5" hidden="1"/><row r="6"><c r="A6" t="inlineStr">'
        '<is><t>row6</t></is></c></row></sheetData>')
    items["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)


class WriteCellsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="cbp_xlsx_patch_"))
        self.path = self.dir / "book.xlsx"
        _make_xlsx(self.path)

    def test_writes_values_roundtrip(self):
        n = write_cells(self.path, [["v1", "v2"], ["v3", None]], sheet="Sheet1",
                        start_cell="B2")
        self.assertEqual(n, 3)
        from openpyxl import load_workbook
        wb = load_workbook(str(self.path))
        ws = wb["Sheet1"]
        self.assertEqual(ws["A1"].value, "hello", "原有单元格不动")
        self.assertEqual(ws["B2"].value, "v1")
        self.assertEqual(ws["C2"].value, "v2")
        self.assertEqual(ws["B3"].value, "v3")
        self.assertIsNone(ws["C3"].value)

    def test_untouched_entries_keep_metadata(self):
        with zipfile.ZipFile(self.path) as zf:
            before = {i.filename: (i.date_time, i.compress_type)
                      for i in zf.infolist()}
        write_cells(self.path, [["x"]], sheet="Sheet1", start_cell="A5")
        with zipfile.ZipFile(self.path) as zf:
            after = {i.filename: (i.date_time, i.compress_type)
                     for i in zf.infolist()}
        # 条目集合不变（没有新增/丢失）
        self.assertEqual(set(before), set(after))
        for name in before:
            self.assertEqual(before[name], after[name],
                             f"{name} 的元数据被改写（date_time/compress_type）")


class SelfClosingRowTests(unittest.TestCase):
    """AUD-P0-11：自闭合 <row/> 不得被跨行匹配吞掉（审核 ⑥-2）。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="cbp_xlsx_selfrow_"))
        self.path = self.dir / "book.xlsx"
        _make_xlsx_with_self_closing(self.path)

    def _sheet_xml(self) -> str:
        with zipfile.ZipFile(self.path) as zf:
            return zf.read("xl/worksheets/sheet1.xml").decode("utf-8")

    def test_write_to_self_closing_row_keeps_next_row_intact(self):
        # 写第 5 行（自闭合行）：展开成完整行，第 6 行的数据与行号必须原样保留
        n = write_cells(self.path, [["v5"]], sheet="Sheet1", start_cell="A5")
        self.assertEqual(n, 1)
        xml = self._sheet_xml()
        from openpyxl import load_workbook
        wb = load_workbook(str(self.path))
        ws = wb["Sheet1"]
        self.assertEqual(ws["A5"].value, "v5", "自闭合行应被展开并写入")
        self.assertEqual(ws["A6"].value, "row6", "第 6 行不能被吞掉")
        self.assertEqual(xml.count('r="6"'), 1, "不能出现重复行号")
        # 文件必须是合法 XML：自闭合标签后不能跟子节点
        from xml.etree import ElementTree as ET
        ET.fromstring(xml)

    def test_write_to_row_after_self_closing_is_not_silently_dropped(self):
        # 写第 6 行（跟在自闭合行后面的完整行）：写入必须生效而不是被静默忽略
        n = write_cells(self.path, [["v6"]], sheet="Sheet1", start_cell="A6")
        self.assertEqual(n, 1)
        from openpyxl import load_workbook
        wb = load_workbook(str(self.path))
        ws = wb["Sheet1"]
        self.assertEqual(ws["A6"].value, "v6", "写第 6 行不能被静默忽略")

    def test_insert_new_row_after_tail_self_closing(self):
        # 文件末尾的孤立自闭合行：新行插入其后，不得产生重复行号
        with zipfile.ZipFile(self.path) as zin:
            items = {i.filename: zin.read(i.filename) for i in zin.infolist()}
        xml = items["xl/worksheets/sheet1.xml"].decode("utf-8")
        xml = xml.replace('<row r="6"><c r="A6" t="inlineStr"><is><t>row6</t></is></c></row>',
                          '<row r="6"/>')
        items["xl/worksheets/sheet1.xml"] = xml.encode("utf-8")
        with zipfile.ZipFile(self.path, "w", zipfile.ZIP_DEFLATED) as zout:
            for name, data in items.items():
                zout.writestr(name, data)
        n = write_cells(self.path, [["v7"]], sheet="Sheet1", start_cell="A7")
        self.assertEqual(n, 1)
        xml2 = self._sheet_xml()
        self.assertEqual(xml2.count('r="7"'), 1, "新行行号唯一")
        from openpyxl import load_workbook
        wb = load_workbook(str(self.path))
        ws = wb["Sheet1"]
        self.assertEqual(ws["A7"].value, "v7")


if __name__ == "__main__":
    unittest.main()
