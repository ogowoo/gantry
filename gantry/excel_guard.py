"""Excel 数据范围探测与安全读取。

核心原则：**Excel 声明的 UsedRange / max_row / max_column 不等于真实数据范围。**
本模块直接流式解析 xlsx 的 sheet XML，按预算识别真实非空单元格，避免为了
判断范围而把 100 万行 × 16384 列全部实例化。

限制：
- 扫描有 max_cells / max_rows / max_seconds 预算；超限返回 truncated + 低置信度；
- 不修改原文件；
- 复杂日期/格式仍由 openpyxl 读取，本模块只负责范围和异常探测。
"""
from __future__ import annotations

import time
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _split_ref(ref: str) -> tuple[int, int] | None:
    """A1 -> (row, col)。"""
    s = "".join(ch for ch in str(ref or "") if ch.isalnum() and not ch.isdigit())
    digits = "".join(ch for ch in str(ref or "") if ch.isdigit())
    if not s or not digits:
        return None
    col = 0
    for ch in s.upper():
        if not ("A" <= ch <= "Z"):
            return None
        col = col * 26 + (ord(ch) - 64)
    return int(digits), col


def _range_bounds(ref: str) -> tuple[int, int, int, int] | None:
    s = str(ref or "").strip()
    if not s:
        return None
    if ":" not in s:
        p = _split_ref(s)
        return (p[0], p[1], p[0], p[1]) if p else None
    a, b = s.split(":", 1)
    pa, pb = _split_ref(a), _split_ref(b)
    if not pa or not pb:
        return None
    return (min(pa[0], pb[0]), min(pa[1], pb[1]),
            max(pa[0], pb[0]), max(pa[1], pb[1]))


@dataclass
class SheetScan:
    name: str
    declared: str = ""
    actual_rows: list[int] = field(default_factory=list)
    col_counts: dict[int, int] = field(default_factory=dict)
    cells_seen: int = 0
    rows_seen: int = 0
    formula_cells: int = 0
    empty_formula_cells: int = 0
    styled_empty_cells: int = 0
    merged: int = 0
    hidden_rows: int = 0
    hidden_cols: int = 0
    truncated: bool = False
    error: str = ""
    first_rows: list[list[str]] = field(default_factory=list)
    first_row_numbers: list[int] = field(default_factory=list)

    @property
    def min_row(self) -> int:
        return min(self.actual_rows) if self.actual_rows else 0

    @property
    def max_row(self) -> int:
        return max(self.actual_rows) if self.actual_rows else 0

    @property
    def min_col(self) -> int:
        return min(self.col_counts) if self.col_counts else 0

    @property
    def max_col(self) -> int:
        return max(self.col_counts) if self.col_counts else 0

    @property
    def row_count(self) -> int:
        return len(self.actual_rows)

    @property
    def col_count(self) -> int:
        return len(self.col_counts)


_SHARED_STRINGS_MAX_BYTES = 32 * 1024 * 1024  # 32MB：超出说明共享字符串表异常


def _read_shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        info = z.getinfo("xl/sharedStrings.xml")
    except KeyError:
        return []
    if info.file_size > _SHARED_STRINGS_MAX_BYTES:
        from .tools import ToolError
        raise ToolError(
            f"xl/sharedStrings.xml 解压后约 {info.file_size / (1024*1024):.0f} MB，"
            f"超过 {_SHARED_STRINGS_MAX_BYTES // (1024*1024)} MB 预算，已中止探测")
    out: list[str] = []
    # 流式 iterparse（而非 ET.fromstring 全量 DOM）：大共享字符串表内存占用
    # 从 ~3 倍峰值降到常数级，速度也更快；截断/损坏的表按空表处理。
    try:
        with z.open("xl/sharedStrings.xml") as f:
            for event, el in ET.iterparse(f, events=("end",)):
                if _local(el.tag) != "si":
                    continue
                parts = [t.text or "" for t in el.iter() if _local(t.tag) == "t"]
                out.append("".join(parts))
                el.clear()
    except ET.ParseError:
        return []
    return out


def _sheet_paths(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """返回 [(sheet_name, xml_path)]，尽量解析 workbook/rels；失败按文件名排序。"""
    names: dict[str, str] = {}
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    except Exception:
        wb = rels = None
    if wb is not None and rels is not None:
        rel_map: dict[str, str] = {}
        for rel in rels:
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target") or ""
            if rid:
                rel_map[rid] = target.lstrip("/")
        for el in wb.iter():
            if _local(el.tag) != "sheet":
                continue
            name = el.attrib.get("name") or ""
            rid = ""
            for k, v in el.attrib.items():
                if _local(k) == "id":
                    rid = v
                    break
            target = rel_map.get(rid, "")
            if target:
                if not target.startswith("xl/"):
                    target = "xl/" + target
                names[name] = target
    if names:
        return list(names.items())
    # 兜底：按 sheetN.xml 文件名顺序
    paths = sorted(n for n in z.namelist()
                   if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    return [(Path(p).stem, p) for p in paths]


def _cell_value(c: ET.Element, shared: list[str]) -> tuple[str, bool]:
    """返回 (显示值, 是否公式)，尽量只读 value，不触发 openpyxl。"""
    typ = c.attrib.get("t", "")
    formula = False
    val = ""
    for child in c:
        ln = _local(child.tag)
        if ln == "f":
            formula = True
        elif ln == "v":
            val = child.text or ""
        elif ln == "is":
            val = "".join(t.text or "" for t in child.iter() if _local(t.tag) == "t")
    if typ == "s" and val.isdigit():
        i = int(val)
        if 0 <= i < len(shared):
            val = shared[i]
    if typ == "b":
        val = "TRUE" if val == "1" else "FALSE"
    return val, formula


def _scan_sheet(z: zipfile.ZipFile, name: str, xml_path: str, shared: list[str],
                *, max_cells: int, max_rows: int, max_seconds: float,
                preview_cols: int = 20) -> SheetScan:
    scan = SheetScan(name=name)
    started = time.monotonic()
    row_cells: dict[int, int] = {}
    preview: dict[int, list[str]] = {}
    valued_cells = 0  # 预算只按“有值/公式”的单元格计：样式空单元格不计入，
    # 否则不规范文件（大量残留格式格）会迅速烧光 max_cells，真实范围测不出来。
    try:
        with z.open(xml_path) as f:
            # 只监听 end：dimension 是自闭合标签，end 事件上属性同样可读，
            # 省掉 start 事件近乎减半 iterparse 回调开销。
            for event, el in ET.iterparse(f, events=("end",)):
                ln = _local(el.tag)
                if ln == "dimension":
                    scan.declared = el.attrib.get("ref", "") or ""
                elif ln == "row":
                    scan.rows_seen += 1
                    rref = el.attrib.get("r")
                    try:
                        rnum = int(rref) if rref else scan.rows_seen
                    except ValueError:
                        rnum = scan.rows_seen
                    if el.attrib.get("hidden") in ("1", "true"):
                        scan.hidden_rows += 1
                    has_row = False
                    for c in el:
                        if _local(c.tag) != "c":
                            continue
                        scan.cells_seen += 1
                        ref = c.attrib.get("r") or ""
                        pos = _split_ref(ref)
                        if not pos:
                            continue
                        row, col = pos
                        val, formula = _cell_value(c, shared)
                        if formula:
                            scan.formula_cells += 1
                            if not str(val).strip():
                                scan.empty_formula_cells += 1
                        has_val = bool(str(val).strip()) or formula
                        if has_val:
                            valued_cells += 1
                            has_row = True
                            row_cells[row] = row_cells.get(row, 0) + 1
                            scan.col_counts[col] = scan.col_counts.get(col, 0) + 1
                        elif c.attrib.get("s"):
                            scan.styled_empty_cells += 1
                        if str(val).strip():
                            if row not in preview and len(preview) < 3:
                                preview[row] = []
                            if row in preview and len(preview[row]) < preview_cols:
                                preview[row].append(str(val))
                    if has_row and rnum not in scan.actual_rows:
                        scan.actual_rows.append(rnum)
                    el.clear()
                elif ln in ("mergeCell",):
                    scan.merged += 1
                elif ln == "col":
                    if el.attrib.get("hidden") in ("1", "true"):
                        scan.hidden_cols += 1
                if valued_cells >= max_cells or scan.cells_seen >= max_cells \
                        or scan.rows_seen >= max_rows:
                    scan.truncated = True
                    break
                if time.monotonic() - started > max_seconds:
                    scan.truncated = True
                    break
    except Exception as exc:  # noqa: BLE001
        scan.error = f"{type(exc).__name__}: {exc}"
    if preview:
        _rows = sorted(preview)
        scan.first_rows = [preview[r] for r in _rows[:3]]
        scan.first_row_numbers = _rows[:3]
    return scan


def _regions(scan: SheetScan, gap: int = 6) -> list[dict[str, Any]]:
    """按大于 gap 的空行切分连续数据区；不把不连续区域当成一张表。"""
    rows = sorted(scan.actual_rows)
    if not rows:
        return []
    blocks: list[list[int]] = [[rows[0], rows[0]]]
    for r in rows[1:]:
        if r - blocks[-1][1] > gap:
            blocks.append([r, r])
        else:
            blocks[-1][1] = r
    out = []
    for i, (a, b) in enumerate(blocks, 1):
        out.append({"index": i, "start_row": a, "end_row": b,
                    "rows": b - a + 1, "min_col": scan.min_col,
                    "max_col": scan.max_col})
    return out


def _looks_number(s: str) -> bool:
    t = str(s).strip().replace(",", "").replace("%", "")
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _header_info(scan: SheetScan) -> dict[str, Any]:
    """第一行是否为表头：非空文本比例、唯一性、数值/日期噪声。"""
    if not scan.first_rows:
        return {"present": False, "row": 0, "values": [], "confidence": 0.0}
    vals = [str(v).strip() for v in scan.first_rows[0] if str(v).strip()]
    if not vals:
        return {"present": False, "row": 0, "values": [], "confidence": 0.0}
    text_n = sum(1 for v in vals if not _looks_number(v))
    uniq = len(set(vals))
    ratio = text_n / max(1, len(vals))
    confidence = round(min(0.98, 0.35 + ratio * 0.45 + (uniq / max(1, len(vals))) * 0.18), 2)
    if len(vals) < 2:
        confidence = min(confidence, 0.45)
    return {"present": confidence >= 0.55,
            "row": scan.first_row_numbers[0] if scan.first_row_numbers else scan.min_row,
            "values": vals[:50], "confidence": confidence}


def _anomalies(scan: SheetScan) -> list[str]:
    out: list[str] = []
    declared = _range_bounds(scan.declared)
    if scan.error:
        out.append("scan_error")
        return out
    if not scan.actual_rows:
        if scan.declared:
            out.append("declared_but_empty")
        return out
    if declared:
        d_rows = declared[2] - declared[0] + 1
        d_cols = declared[3] - declared[1] + 1
        a_rows = max(1, scan.max_row - min(scan.min_row, scan.max_row) + 1)
        a_cols = max(1, scan.max_col - min(scan.min_col, scan.max_col) + 1)
        if d_rows > max(a_rows * 2 + 100, 5000) or d_cols > max(a_cols * 2 + 50, 500):
            out.append("used_range_inflated")
        if declared[2] > 1_000_000 or declared[3] > 10_000:
            out.append("declared_huge")
    if scan.truncated:
        out.append("scan_truncated")
    if scan.empty_formula_cells:
        out.append("empty_formula_cells")
    if scan.merged:
        out.append("merged_cells")
    if scan.hidden_rows or scan.hidden_cols:
        out.append("hidden_rows_or_cols")
    if scan.styled_empty_cells >= 20:
        out.append("distant_formatted_cells")
    # 数据行之间的大空洞
    rows = sorted(scan.actual_rows)
    gaps = [(b - a - 1) for a, b in zip(rows, rows[1:]) if b - a > 6]
    if gaps:
        out.append("data_gaps")
    regions = _regions(scan)
    if len(regions) > 1:
        out.append("multiple_regions")
    header = _header_info(scan)
    if header.get("confidence", 0) < 0.55:
        out.append("weak_header")
    # 只有一两个单元格的列，疑似残留格式/备注列
    sparse = [c for c, n in scan.col_counts.items() if n <= 1]
    if len(sparse) >= 3 and len(scan.col_counts) > 3:
        out.append("sparse_columns")
    return out


def _confidence(scan: SheetScan, anomalies: list[str]) -> float:
    if scan.error:
        return 0.0
    if not scan.actual_rows:
        return 0.9
    conf = 0.99
    if "scan_truncated" in anomalies:
        conf -= 0.45
    if "used_range_inflated" in anomalies:
        conf -= 0.05
    if "merged_cells" in anomalies:
        conf -= 0.12
    if "data_gaps" in anomalies:
        conf -= 0.1
    if "sparse_columns" in anomalies:
        conf -= 0.05
    if "multiple_regions" in anomalies:
        conf -= 0.15
    if "weak_header" in anomalies:
        conf -= 0.08
    return round(max(0.05, min(0.99, conf)), 2)


def _range_str(scan: SheetScan) -> str:
    if not scan.actual_rows:
        return ""
    from openpyxl.utils import get_column_letter
    return (f"{get_column_letter(max(1, scan.min_col))}{scan.min_row}:"
            f"{get_column_letter(max(1, scan.max_col))}{scan.max_row}")


def _skipped_sheet(name: str) -> dict[str, Any]:
    """超全局预算后被跳过的表：标记 truncated，字段给安全默认值。"""
    return {"sheet": name, "declared_range": "", "detected_range": "",
            "actual_rows": 0, "actual_columns": 0,
            "min_row": 0, "max_row": 0, "min_col": 0, "max_col": 0,
            "actual_cells": 0, "formula_cells": 0, "empty_formula_cells": 0,
            "merged_cells": 0, "hidden_rows": 0, "hidden_columns": 0,
            "styled_empty_cells": 0, "truncated": True,
            "anomalies": ["scan_truncated"], "confidence": 0.1,
            "header": {"present": False, "row": 0, "values": [], "confidence": 0.0},
            "header_row": 0, "header_confidence": 0.0, "header_sample": [],
            "data_start_row": 0, "regions": [], "region_count": 0, "error": ""}


def inspect_workbook(path: str | Path, sheet: str = "", *,
                     max_cells: int = 200_000, max_rows: int = 200_000,
                     max_seconds: float = 8.0) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": "文件不存在", "sheets": []}
    if p.suffix.lower() not in (".xlsx", ".xlsm"):
        return {"ok": False, "error": "目前只支持 .xlsx/.xlsm 的结构探测",
                "sheets": []}
    started = time.monotonic()
    try:
        with zipfile.ZipFile(p) as z:
            shared = _read_shared_strings(z)
            pairs = _sheet_paths(z)
            if sheet:
                pairs = [(n, x) for n, x in pairs if n == sheet]
                if not pairs:
                    return {"ok": False, "error": f"工作表不存在: {sheet}", "sheets": []}
            sheets = []
            budget_exceeded = False
            for name, xml_path in pairs:
                if not budget_exceeded and time.monotonic() - started > max_seconds:
                    # 全局时间预算用尽：剩余表不再扫，直接标 truncated 跳过，
                    # 避免 N 张表每张都各烧一遍 max_seconds。
                    budget_exceeded = True
                if budget_exceeded:
                    sheets.append(_skipped_sheet(name))
                    continue
                scan = _scan_sheet(z, name, xml_path, shared, max_cells=max_cells,
                                   max_rows=max_rows, max_seconds=max_seconds)
                anomalies = _anomalies(scan)
                regions = _regions(scan)
                header = _header_info(scan)
                sheets.append({
                    "sheet": name,
                    "declared_range": scan.declared,
                    "detected_range": _range_str(scan),
                    "actual_rows": scan.row_count,
                    "actual_columns": scan.col_count,
                    "min_row": scan.min_row, "max_row": scan.max_row,
                    "min_col": scan.min_col, "max_col": scan.max_col,
                    "actual_cells": len(scan.actual_rows) and sum(scan.col_counts.values()) or 0,
                    "formula_cells": scan.formula_cells,
                    "empty_formula_cells": scan.empty_formula_cells,
                    "merged_cells": scan.merged,
                    "hidden_rows": scan.hidden_rows,
                    "hidden_columns": scan.hidden_cols,
                    "styled_empty_cells": scan.styled_empty_cells,
                    "truncated": scan.truncated,
                    "anomalies": anomalies,
                    "confidence": _confidence(scan, anomalies),
                    "header": header,
                    "header_row": header.get("row", 0),
                    "header_confidence": header.get("confidence", 0.0),
                    "header_sample": scan.first_rows[0][:20] if scan.first_rows else [],
                    "data_start_row": ((header.get("row") or scan.min_row) + 1
                                       if header.get("present") else scan.min_row),
                    "regions": regions,
                    "region_count": len(regions),
                    "error": scan.error,
                })
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "sheets": [],
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    return {"ok": True, "path": str(p), "sheets": sheets,
            "needs_confirmation": any(
                float(s.get("confidence") or 0) < 0.55
                or "multiple_regions" in (s.get("anomalies") or [])
                for s in sheets),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "budget": {"max_cells": max_cells, "max_rows": max_rows,
                       "max_seconds": max_seconds}}


def recommended_scan(report: dict[str, Any], sheet: str = "") -> dict[str, Any]:
    """从报告里取一个可用于安全读取的 sheet；低置信度调用方应询问用户。"""
    sheets = report.get("sheets") or []
    if sheet:
        sheets = [s for s in sheets if s.get("sheet") == sheet]
    if not sheets:
        return {}
    s = sheets[0]
    return {
        "sheet": s.get("sheet"), "start_row": max(1, int(s.get("min_row") or 1)),
        "end_row": int(s.get("max_row") or 0),
        "start_col": max(1, int(s.get("min_col") or 1)),
        "end_col": int(s.get("max_col") or 0),
        "confidence": s.get("confidence", 0.0),
        "anomalies": s.get("anomalies") or [],
        "regions": s.get("regions") or [],
        "header": s.get("header") or {},
        "header_row": s.get("header_row", 0),
        "data_start_row": s.get("data_start_row", 0),
        "needs_confirmation": bool(s.get("confidence", 0) < 0.55
                                   or "multiple_regions" in (s.get("anomalies") or [])),
    }
