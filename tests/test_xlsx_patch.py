"""xlsx_patch.write_cells 回归：写入正确 + 未修改的 zip 条目保留原元数据。

对应审查报告 M22：旧实现 zout.writestr(item.filename, data) 只传文件名，
条目时间戳/压缩方式全部丢成默认值（Excel 打开即提示"包被修改"，diff 全红）。
"""
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


if __name__ == "__main__":
    unittest.main()
