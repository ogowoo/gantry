# -*- coding: utf-8 -*-
"""大 Excel 的快速值写入（直接改 xlsx/xlsm 内部 XML）。

openpyxl 的普通 load_workbook() 会按工作表 dimension 建满单元格对象：用户的
Tracker 文件只有 3.4 MB、实际约 555k 个有内容单元格，却因为几个远端空样式
单元格把 dimension 撑到 XEJ5379（约 8800 万格），一次 load 要 15 秒。

这里只改目标 sheet 的 XML：保留样式、公式、条件格式、数据验证和 VBA，不重建
整个工作簿。只支持值/公式（日期等复杂类型回退 openpyxl）。失败时抛
FastPathUnsupported，由调用方回退，不静默丢数据。
"""
from __future__ import annotations

import math
import os
import posixpath
import re
import tempfile
import zipfile
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from openpyxl.utils import get_column_letter
from openpyxl.utils.cell import column_index_from_string

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

ROW_RE = re.compile(r"<row\b[^>]*\br=\"(\d+)\"[^>]*>.*?</row>", re.S)
CELL_RE = re.compile(r"<c\b(?P<attrs>[^>]*?)(?:/>|>(?P<body>.*?)</c>)", re.S)
DIM_RE = re.compile(r"<dimension\b[^>]*\bref=\"([^\"]+)\"[^>]*/>")
_COL_RE = re.compile(r"^([A-Z]+)")
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class FastPathUnsupported(Exception):
    """当前写入不适合 XML 快路径，调用方应回退 openpyxl。"""


def _col(ref: str) -> int:
    m = _COL_RE.match(ref or "")
    if not m:
        raise FastPathUnsupported(f"无效单元格: {ref}")
    return column_index_from_string(m.group(1))


def _xml_text(value: Any) -> str:
    text = str(value)
    text = _BAD_XML.sub("", text)
    return escape(text)


def _sheet_paths(zf: zipfile.ZipFile) -> tuple[list[tuple[str, str]], int]:
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except Exception as exc:                    # noqa: BLE001
        raise FastPathUnsupported(f"workbook XML 不可读: {exc}") from exc
    rel_map = {}
    for rel in rels.findall(f"{{{NS_PKG_REL}}}Relationship"):
        rel_map[rel.get("Id")] = rel.get("Target", "")
    sheets: list[tuple[str, str]] = []
    for node in wb.findall(f"{{{NS_MAIN}}}sheets/{{{NS_MAIN}}}sheet"):
        rid = node.get(f"{{{NS_REL}}}id")
        target = rel_map.get(rid, "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            path = posixpath.normpath(posixpath.join("xl", target))
        sheets.append((node.get("name", ""), path))
    active = 0
    view = wb.find(f"{{{NS_MAIN}}}bookViews/{{{NS_MAIN}}}workbookView")
    if view is not None:
        try:
            active = int(view.get("activeTab", "0"))
        except ValueError:
            active = 0
    return sheets, active


def _cell_xml(ref: str, value: Any, old_attrs: str = "") -> str:
    style = ""
    m = re.search(r'\bs="[^"]*"', old_attrs or "")
    if m:
        style = " " + m.group(0)
    attrs = f' r="{ref}"{style}'
    if value is None:
        return f"<c{attrs}/>"
    if isinstance(value, bool):
        return f'<c{attrs} t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (date, datetime, time)):
        raise FastPathUnsupported("日期/时间值回退 openpyxl（需要数字格式）")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise FastPathUnsupported("NaN/Inf 回退 openpyxl")
        return f"<c{attrs}><v>{value!r}</v></c>"
    text = str(value)
    if text.startswith("="):
        return f"<c{attrs}><f>{_xml_text(text[1:])}</f></c>"
    return (f'<c{attrs} t="inlineStr"><is><t xml:space="preserve">'
            f"{_xml_text(text)}</t></is></c>")


def _rewrite_row(row_xml: str, writes: dict[int, Any]) -> str:
    open_tag = re.match(r"<row\b[^>]*>", row_xml)
    if not open_tag:
        raise FastPathUnsupported("无法解析 row 标签")
    prefix = open_tag.group(0)
    rm = re.search(r'\br="(\d+)"', prefix)
    if not rm:
        raise FastPathUnsupported("row 缺少行号")
    row_no = int(rm.group(1))
    inner = row_xml[open_tag.end():-len("</row>")]
    existing: dict[int, str] = {}
    for m in CELL_RE.finditer(inner):
        attrs = m.group("attrs")
        rm = re.search(r'\br="([A-Z]+\d+)"', attrs or "")
        if not rm:
            continue
        idx = _col(rm.group(1))
        existing[idx] = m.group(0)
    for idx, value in writes.items():
        old = existing.get(idx, "")
        attrs = re.search(r'<c\b([^>]*)', old).group(1) if old else ""
        new = _cell_xml(f"{get_column_letter(idx)}{row_no}", value, attrs)
        if value is None and not old:
            continue
        existing[idx] = new
    cells = "".join(existing[i] for i in sorted(existing))
    return prefix + cells + "</row>"


def write_cells(path: str | Path, rows: list, sheet: str = "",
                start_cell: str = "A1") -> int:
    """把二维 rows 写到 start_cell，返回写入单元格数；不改其他 XML。"""
    target = Path(path)
    if target.suffix.lower() not in (".xlsx", ".xlsm"):
        raise FastPathUnsupported("仅支持 xlsx/xlsm")
    if not isinstance(rows, list) or not rows:
        raise FastPathUnsupported("rows 为空")
    if not target.is_file():
        raise FastPathUnsupported("目标文件不存在")
    try:
        from openpyxl.utils.cell import coordinate_from_string
        col_s, row_s = coordinate_from_string(start_cell)
        col0 = column_index_from_string(col_s)
    except Exception as exc:                    # noqa: BLE001
        raise FastPathUnsupported(f"起始单元格无效: {exc}") from exc

    with zipfile.ZipFile(target) as zf:
        sheets, active = _sheet_paths(zf)
        if not sheets:
            raise FastPathUnsupported("工作簿没有工作表")
        if sheet:
            match = next((p for n, p in sheets if n == sheet), None)
            if match is None:
                raise FastPathUnsupported(f"工作表不存在: {sheet}")
            sheet_path = match
        else:
            if active >= len(sheets):
                active = 0
            sheet_path = sheets[active][1]
        xml = zf.read(sheet_path).decode("utf-8")
        # 先收集所有写入；日期/异常值会在真正构造 cell 时抛
        row_writes: dict[int, dict[int, Any]] = {}
        written = 0
        for ri, row in enumerate(rows):
            if not isinstance(row, list):
                row = [row]
            for ci, value in enumerate(row):
                row_no = row_s + ri
                col_no = col0 + ci
                if col_no < 1 or row_no < 1:
                    raise FastPathUnsupported("写入位置超出范围")
                row_writes.setdefault(row_no, {})[col_no] = value
                if value is not None:
                    written += 1

        # 已有行：批量重写；新行：按行号插入
        new_rows: dict[int, str] = {}
        for row_no, writes in row_writes.items():
            m = re.search(rf'<row\b[^>]*\br="{row_no}"[^>]*>.*?</row>', xml, re.S)
            if m:
                new_rows[row_no] = _rewrite_row(m.group(0), writes)
        def _repl(m: re.Match) -> str:
            n = int(m.group(1))
            return new_rows.get(n, m.group(0))
        if new_rows:
            xml = ROW_RE.sub(_repl, xml)

        for row_no in sorted(row_writes):
            if row_no in new_rows:
                continue
            row = "".join(_cell_xml(f"{get_column_letter(c)}{row_no}", v)
                          for c, v in sorted(row_writes[row_no].items())
                          if v is not None)
            if not row:
                continue
            new_row = f'<row r="{row_no}">{row}</row>'
            insert_at = None
            for m in ROW_RE.finditer(xml):
                if int(m.group(1)) > row_no:
                    insert_at = m.start()
                    break
            if insert_at is None:
                insert_at = xml.find("</sheetData>")
            if insert_at < 0:
                raise FastPathUnsupported("sheetData 不可用")
            xml = xml[:insert_at] + new_row + xml[insert_at:]

        # 更新 dimension（没有就补在 sheetViews 前）
        max_row = row_s + len(rows) - 1
        max_col = col0 + max((len(r) if isinstance(r, list) else 1) for r in rows) - 1
        new_dim = f"A1:{get_column_letter(max_col)}{max_row}"
        m = DIM_RE.search(xml)
        if m:
            old = m.group(1)
            try:
                end = old.split(":")[-1]
                ec = _col(end)
                er = int(re.sub(r"[A-Z]+", "", end) or "1")
                max_col = max(max_col, ec)
                max_row = max(max_row, er)
            except Exception:                    # noqa: BLE001
                pass
            new_dim = f"A1:{get_column_letter(max_col)}{max_row}"
            xml = xml[:m.start()] + f'<dimension ref="{new_dim}"/>' + xml[m.end():]
        else:
            anchor = "<sheetViews" if "<sheetViews" in xml else "<sheetData"
            pos = xml.find(anchor)
            if pos >= 0:
                xml = xml[:pos] + f'<dimension ref="{new_dim}"/>' + xml[pos:]

        fd, tmp_name = tempfile.mkstemp(prefix=".gantry-xlsx-", suffix=target.suffix,
                                       dir=str(target.parent))
        os.close(fd)
        # Windows 不允许替换仍被打开的文件：先释放读句柄再写 ZIP/替换。
        zf.close()
        try:
            with zipfile.ZipFile(target) as zin, \
                    zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == sheet_path:
                        data = xml.encode("utf-8")
                    zout.writestr(item.filename, data)
            os.replace(tmp_name, target)
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    return written
