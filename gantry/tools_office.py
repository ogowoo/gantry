"""tools_office.py — Office 文档工具（ToolRegistry 的 mixin）：Excel / Word / PowerPoint / PDF 读写。

从 tools.py 拆出（第四批解耦）：方法体原样搬入 mixin，运行时行为不变。
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import inspect
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import editmatch

from .tools import ToolError  # noqa: F401


def _range_str(g: dict[str, Any]) -> str:
    from openpyxl.utils import get_column_letter
    if not g:
        return ""
    return (f"{get_column_letter(max(1, int(g.get('start_col') or 1)))}"
            f"{g.get('start_row')}:"
            f"{get_column_letter(max(1, int(g.get('end_col') or 1)))}"
            f"{g.get('end_row')}")


def _read_range_sync(target: Path, sheet: str, start_row: int, end_row: int,
                     max_col: int) -> list[list[str]]:
    import openpyxl
    wb = openpyxl.load_workbook(target, data_only=True, read_only=True)
    try:
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
        rows = []
        for r in ws.iter_rows(min_row=max(1, start_row), max_row=max(1, end_row),
                              max_col=max(1, max_col), values_only=True):
            vals = ["" if c is None else str(c) for c in r]
            while vals and vals[-1] == "":
                vals.pop()
            rows.append(vals)
        return rows
    finally:
        wb.close()


def _inspect_for_read(target: Path, sheet: str) -> dict[str, Any]:
    from .excel_guard import inspect_workbook
    return inspect_workbook(target, sheet, max_cells=200_000,
                            max_rows=200_000, max_seconds=6.0)


def _safe_bounds(g: dict[str, Any]) -> dict[str, int] | None:
    """只有当探测给出明确实际范围时才替换 openpyxl 的 max_row/max_column。"""
    if not g or g.get("error"):
        return None
    try:
        start_row = int(g.get("min_row") or 0)
        end_row = int(g.get("max_row") or 0)
        start_col = int(g.get("min_col") or 0)
        end_col = int(g.get("max_col") or 0)
    except (TypeError, ValueError):
        return None
    if end_row <= 0 or end_col <= 0:
        return None
    if float(g.get("confidence") or 0) < 0.15:
        return None
    return {"start_row": max(1, start_row), "end_row": end_row,
            "start_col": max(1, start_col), "end_col": end_col}


class OfficeMixin:
    def _office_allowed(self):
        if not self.allow_office:
            raise ToolError("Office 工具已在 config.json 中禁用（allow_office=false）")

    @staticmethod
    def _int_arg(value, default: int, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ToolError(f"{name} 必须是整数")

    async def excel_preview(self, path: str, sheet: str = "",
                            max_rows: int = 5) -> dict[str, Any]:
        """Excel 预览：真实范围 + 表头 + 前几行 + 数据区域。"""
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        if target.suffix.lower() == ".xls":
            raise ToolError("旧版 .xls 暂不支持；请先另存为 .xlsx 或 .xlsm")
        limit = max(1, min(self._int_arg(max_rows, 5, "max_rows"), 30))
        report = await asyncio.to_thread(_inspect_for_read, target, sheet)
        from .excel_guard import recommended_scan
        guard = recommended_scan(report, sheet)
        if not guard or not guard.get("end_row"):
            return {"path": path, "inspection": report, "rows": [],
                    "header": [], "regions": [], "warnings": ["没有识别到数据区域"]}
        end = min(guard["end_row"], guard["start_row"] + limit - 1)
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(_read_range_sync, target, guard["sheet"],
                                  guard["start_row"], end,
                                  min(1000, guard["end_col"])),
                timeout=max(1.0, timeout))
        except asyncio.TimeoutError:
            raise ToolError(f"Excel 预览超过 {int(timeout)} 秒，已停止")
        return {"path": path, "sheet": guard["sheet"],
                "inspection": report, "detected_range": _range_str(guard),
                "header": (guard.get("header") or {}).get("values") or [],
                "header_row": guard.get("header_row", 0),
                "regions": guard.get("regions") or [],
                "rows": rows,
                "warnings": [a for a in (guard.get("anomalies") or [])
                             if a in ("multiple_regions", "weak_header",
                                      "used_range_inflated", "scan_truncated")]}

    async def excel_read_chunk(self, path: str, sheet: str = "", chunk_rows: int = 500,
                               resume: bool = True, reset: bool = False,
                               region: int = 0) -> dict[str, Any]:
        """分块读取 + 检查点续跑；不一次读完整表。"""
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        if target.suffix.lower() == ".xls":
            raise ToolError("旧版 .xls 暂不支持；请先另存为 .xlsx 或 .xlsm")
        size = max(1, min(self._int_arg(chunk_rows, 500, "chunk_rows"), 2000))
        report = await asyncio.to_thread(_inspect_for_read, target, sheet)
        from .excel_guard import recommended_scan
        guard = recommended_scan(report, sheet)
        if not guard or not guard.get("end_row"):
            raise ToolError("没有识别到可读取的数据区域；请先 excel_inspect")
        regions = guard.get("regions") or []
        if region and (not regions or region > len(regions)):
            raise ToolError(f"region={region} 超出范围，可用区域数: {len(regions)}")
        if region and 1 <= region <= len(regions):
            r = regions[region - 1]
            guard = dict(guard, start_row=int(r["start_row"]),
                         end_row=int(r["end_row"]))
        header = (guard.get("header") or {}).get("values") or []
        start = max(guard["start_row"],
                    int(guard.get("data_start_row") or guard["start_row"]))
        cp = self._excel_checkpoint_path(target, guard["sheet"])
        processed = 0
        if resume and not reset and cp.is_file():
            try:
                old = json.loads(cp.read_text(encoding="utf-8"))
                if int(old.get("next_row") or 0) >= start:
                    start = int(old["next_row"])
                processed = int(old.get("processed") or 0)
            except Exception:
                pass
        if start > guard["end_row"]:
            return {"path": path, "sheet": guard["sheet"], "rows": [], "header": header,
                    "done": True, "next_row": start, "processed": processed,
                    "total_rows": max(0, guard["end_row"] - start + 1),
                    "checkpoint": str(cp), "regions": regions}
        end = min(guard["end_row"], start + size - 1)
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        try:
            rows = await asyncio.wait_for(
                asyncio.to_thread(_read_range_sync, target, guard["sheet"], start, end,
                                  min(1000, guard["end_col"])),
                timeout=max(1.0, timeout))
        except asyncio.TimeoutError:
            raise ToolError(f"Excel 分块读取超过 {int(timeout)} 秒，已停止；检查点未推进")
        processed += len(rows)
        done = end >= guard["end_row"]
        cp.parent.mkdir(parents=True, exist_ok=True)
        tmp = cp.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "path": str(target), "sheet": guard["sheet"],
            "next_row": end + 1, "processed": processed, "end_row": guard["end_row"],
            "updated": time.time()}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cp)
        return {"path": path, "sheet": guard["sheet"],
                "rows": rows, "header": header, "done": done,
                "next_row": end + 1, "processed": processed,
                "range": f"{start}:{end}", "checkpoint": str(cp),
                "total_rows": max(0, guard["end_row"] - guard["start_row"] + 1),
                "regions": regions,
                "confidence": guard.get("confidence"),
                "anomalies": guard.get("anomalies") or []}

    def _excel_checkpoint_path(self, target: Path, sheet: str) -> Path:
        try:
            st = target.stat()
            fingerprint = f"{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            fingerprint = ""
        key = hashlib.sha1((str(target.resolve()) + "|" + str(sheet) + "|"
                            + fingerprint).encode("utf-8")).hexdigest()[:20]
        base = Path(self.root) / ".gantry" / "excel_checkpoints"
        return base / f"{key}.json"

    async def read_excel(self, path: str, sheet: str = "", max_rows: int = 200,
                         max_cols: int = 300, safe: bool = True,
                         region: int = 0) -> dict[str, Any]:
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        if target.suffix.lower() == ".xls":
            raise ToolError("旧版 .xls 暂不支持；请先另存为 .xlsx 或 .xlsm")
        limit = max(1, min(self._int_arg(max_rows, 200, "max_rows"), 2000))
        columns = max(1, min(self._int_arg(max_cols, 300, "max_cols"), 1000))
        try:
            region_n = max(0, int(region or 0))
        except (TypeError, ValueError):
            raise ToolError("region 必须是整数")
        report = None
        if safe:
            # 先探测真实范围；不能把 max_row/max_column 当数据边界。
            report = await asyncio.to_thread(
                _inspect_for_read, target, sheet)
            if region_n:
                picked = next((s for s in (report.get("sheets") or [])
                               if not sheet or s.get("sheet") == sheet), None)
                regions = (picked or {}).get("regions") or []
                if region_n > len(regions):
                    raise ToolError(f"region={region_n} 超出范围，可用区域数: {len(regions)}")
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        try:
            out = await asyncio.wait_for(
                asyncio.to_thread(self._read_excel_sync, target, sheet, limit,
                                  columns, report, region_n),
                timeout=max(1.0, timeout))
        except asyncio.TimeoutError:
            raise ToolError(
                f"Excel 读取超过 {int(timeout)} 秒，已停止等待；请用 "
                "excel_inspect/excel_read_chunk 缩小范围")
        return {"path": path, **out}

    async def excel_inspect(self, path: str, sheet: str = "", max_cells: int = 200_000,
                            max_rows: int = 200_000, max_seconds: float = 8.0) -> dict[str, Any]:
        """只探测 Excel 结构，不读取整表：UsedRange 不可信时的第一道门。"""
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        from .excel_guard import inspect_workbook
        try:
            budget = max(1_000, min(int(max_cells), 1_000_000))
            rows_budget = max(1_000, min(int(max_rows), 1_000_000))
            secs = max(0.5, min(float(max_seconds), 30.0))
        except (TypeError, ValueError):
            raise ToolError("max_cells/max_rows/max_seconds 必须是数字")
        report = await asyncio.to_thread(
            inspect_workbook, target, sheet, max_cells=budget, max_rows=rows_budget,
            max_seconds=secs)
        return {"path": path, **report}

    async def excel_validate(self, path: str, sheet: str = "",
                             expect_rows: int = 0,
                             expect_headers: list | str | None = None) -> dict[str, Any]:
        """处理结果验证：重新探测输出文件，确认可打开、范围和行数。"""
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        from .excel_guard import inspect_workbook
        report = await asyncio.to_thread(inspect_workbook, target, sheet,
                                         max_cells=200_000, max_rows=200_000,
                                         max_seconds=8.0)
        sheets = report.get("sheets") or []
        picked = next((s for s in sheets if not sheet or s.get("sheet") == sheet), None)
        checks = []
        if not report.get("ok"):
            checks.append({"check": "openable", "ok": False,
                           "detail": report.get("error") or "无法读取"})
        else:
            checks.append({"check": "openable", "ok": True, "detail": "可读取"})
        if picked:
            actual = int(picked.get("actual_rows") or 0)
            if expect_rows:
                checks.append({"check": "expected_rows", "ok": actual >= int(expect_rows),
                               "detail": f"实际 {actual}，期望至少 {expect_rows}"})
            checks.append({"check": "detected_range", "ok": bool(picked.get("detected_range")),
                           "detail": picked.get("detected_range") or "无数据"})
            if expect_headers:
                wanted = ([str(x) for x in expect_headers]
                          if isinstance(expect_headers, (list, tuple))
                          else [x.strip() for x in str(expect_headers).split(",") if x.strip()])
                actual = [str(x).strip() for x in
                          ((picked.get("header") or {}).get("values") or [])]
                missing = [h for h in wanted if h not in actual]
                checks.append({"check": "expected_headers", "ok": not missing,
                               "detail": f"缺少: {missing}" if missing else "表头齐全"})
        return {"path": path, "ok": all(c["ok"] for c in checks),
                "checks": checks, "report": report}

    @staticmethod
    def _read_excel_sync(target: Path, sheet: str, limit: int,
                         columns: int, report: dict[str, Any] | None = None,
                         region: int = 0) -> dict[str, Any]:
        try:
            import openpyxl
        except ImportError:
            raise ToolError("缺少依赖 openpyxl，请运行 pip install openpyxl")
        wb = openpyxl.load_workbook(target, data_only=True, read_only=True)
        try:
            if sheet and sheet not in wb.sheetnames:
                raise ToolError(f"工作表不存在: {sheet}，可用: {wb.sheetnames}")
            guard_by_sheet = {s.get("sheet"): s for s in ((report or {}).get("sheets") or [])}
            out = {}
            for name in ([sheet] if sheet else wb.sheetnames):
                ws = wb[name]
                g = guard_by_sheet.get(name) or {}
                if region and g.get("regions"):
                    rs = g["regions"]
                    if region <= len(rs):
                        r = rs[region - 1]
                        g = dict(g, min_row=int(r["start_row"]),
                                 max_row=int(r["end_row"]),
                                 min_col=int(r.get("min_col") or g.get("min_col") or 1),
                                 max_col=int(r.get("max_col") or g.get("max_col") or 1))
                guard = _safe_bounds(g)
                rows = []
                if guard:
                    start_row = max(1, min(guard["start_row"], guard["end_row"]))
                    end_row = min(guard["end_row"], start_row + limit - 1)
                    max_col = max(1, min(columns, guard["end_col"]))
                else:
                    start_row, end_row, max_col = 1, limit, columns
                # 只取真实范围的前 limit 行；max_col 限制：远端空样式列不读。
                for r in ws.iter_rows(min_row=start_row, max_row=end_row,
                                      max_col=max_col, values_only=True):
                    vals = ["" if c is None else str(c) for c in r]
                    while vals and vals[-1] == "":
                        vals.pop()
                    rows.append(vals)
                if guard:
                    total = max(0, guard["end_row"] - guard["start_row"] + 1)
                    total_cols = max(0, guard["end_col"] - guard["start_col"] + 1)
                    truncated = guard["end_row"] > end_row
                else:
                    total = int(ws.max_row or len(rows))
                    total_cols = int(ws.max_column or 0)
                    truncated = total > limit
                out[name] = {"rows": rows, "total_rows": total,
                             "total_columns": total_cols,
                             "columns_read": max_col,
                             "used_columns_sample": max((len(x) for x in rows), default=0),
                             "truncated": truncated,
                             "declared_range": g.get("declared_range", ""),
                             "detected_range": g.get("detected_range", ""),
                             "anomalies": g.get("anomalies") or [],
                             "confidence": g.get("confidence")}
            return {"sheets": out,
                    "inspection": (report or {})}
        finally:
            wb.close()

    async def write_excel(self, path: str, rows: list, sheet: str = "", start_cell: str = "A1") -> dict[str, Any]:
        self._office_allowed()
        if not isinstance(rows, list) or not rows:
            raise ToolError("rows 必须是非空二维数组")
        target = self._path(path)
        self._snap("write_excel", path, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = self._hash(target)
        # 大文件优先走 XML 快路径（不再 load 整个工作簿）；失败回退 openpyxl。
        meta = await asyncio.to_thread(
            self._write_excel_sync, target, rows, sheet, start_cell)
        after = self._hash(target)
        return {"path": path, **meta,
                "file_event": {"tool": "write_excel", "path": path,
                               "before_hash": before, "after_hash": after,
                               "result": "ok"}}

    @staticmethod
    def _write_excel_sync(target: Path, rows: list, sheet: str,
                          start_cell: str) -> dict[str, Any]:
        # 1 MB 以上直接改 XML：Tracker 这类文件有几十万真实单元格，
        # openpyxl 全量 load/save 一次约 15 秒，而且会丢掉条件格式扩展。
        fast_ok = (target.is_file() and target.suffix.lower() in (".xlsx", ".xlsm")
                   and target.stat().st_size >= 1_000_000)
        if fast_ok:
            try:
                from .xlsx_patch import FastPathUnsupported, write_cells
                written = write_cells(target, rows, sheet, start_cell)
                return {"sheet": sheet or "", "cells_written": written,
                        "engine": "xml-fast"}
            except FastPathUnsupported:
                pass
            except Exception:
                # XML 补丁失败不是"数据已写"：回退到 openpyxl 重新写一遍。
                pass
        try:
            import openpyxl
            from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
        except ImportError:
            raise ToolError("缺少依赖 openpyxl，请运行 pip install openpyxl")
        try:
            wb = openpyxl.load_workbook(target, keep_vba=target.suffix.lower() == ".xlsm") \
                if target.is_file() else openpyxl.Workbook()
        except Exception as exc:
            raise ToolError(f"无法读取 Excel 文件（可能不是有效的 xlsx）: {exc}")
        try:
            if sheet:
                ws = wb[sheet] if sheet in wb.sheetnames else wb.create_sheet(sheet)
            else:
                ws = wb.active
            try:
                col_s, row_s = coordinate_from_string(start_cell)
                col0 = column_index_from_string(col_s)
            except Exception:
                raise ToolError(f"无效的起始单元格: {start_cell}")
            written = 0
            for ri, row in enumerate(rows):
                if not isinstance(row, list):
                    row = [row]
                for ci, value in enumerate(row):
                    ws.cell(row=row_s + ri, column=col0 + ci, value=value)
                    written += 1
            wb.save(target)
            title = ws.title
        finally:
            wb.close()
        return {"sheet": title, "cells_written": written, "engine": "openpyxl"}

    async def read_docx(self, path: str) -> dict[str, Any]:
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        try:
            import docx
        except ImportError:
            raise ToolError("缺少依赖 python-docx，请运行 pip install python-docx")
        doc = docx.Document(target)
        parts = []
        for p in doc.paragraphs:
            if p.text.strip():
                parts.append(p.text)
        for ti, table in enumerate(doc.tables, 1):
            parts.append(f"[表格 {ti}]")
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        text = "\n".join(parts)
        return {"path": path, "paragraphs": len(doc.paragraphs), "tables": len(doc.tables),
                "content": text[:self.max_read], "truncated": len(text) > self.max_read}

    async def write_docx(self, path: str, paragraphs: list, mode: str = "append") -> dict[str, Any]:
        self._office_allowed()
        if not isinstance(paragraphs, list) or not paragraphs:
            raise ToolError("paragraphs 必须是非空字符串数组")
        if mode not in ("append", "overwrite"):
            raise ToolError("mode 只能是 append 或 overwrite")
        try:
            import docx
        except ImportError:
            raise ToolError("缺少依赖 python-docx，请运行 pip install python-docx")
        target = self._path(path)
        self._snap("write_docx", path, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = self._hash(target)
        if mode == "overwrite" or not target.is_file():
            doc = docx.Document()
        else:
            doc = docx.Document(target)
        for text in paragraphs:
            doc.add_paragraph(str(text))
        doc.save(target)
        return {"path": path, "mode": mode, "paragraphs_added": len(paragraphs),
                "file_event": {"tool": "write_docx", "path": path,
                               "before_hash": before, "after_hash": self._hash(target), "result": "ok"}}

    async def read_pptx(self, path: str) -> dict[str, Any]:
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        try:
            from pptx import Presentation
        except ImportError:
            raise ToolError("缺少依赖 python-pptx，请运行 pip install python-pptx")
        prs = Presentation(target)
        slides = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [shape.text for shape in slide.shapes if shape.has_text_frame and shape.text.strip()]
            notes = ""
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                notes = slide.notes_slide.notes_text_frame.text.strip()
            slides.append({"slide": i, "text": "\n".join(texts), "notes": notes})
        total_chars = sum(len(s["text"]) + len(s["notes"]) for s in slides)
        return {"path": path, "slides": slides, "slide_count": len(slides),
                "truncated": total_chars > self.max_read}

    async def read_pdf(self, path: str) -> dict[str, Any]:
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ToolError("缺少依赖 pypdf，请运行 pip install pypdf")
        reader = PdfReader(str(target))
        pages = []
        used = 0
        truncated = False
        for i, page in enumerate(reader.pages, 1):
            if used >= self.max_read:
                truncated = True
                break
            text = (page.extract_text() or "").strip()
            used += len(text)
            pages.append({"page": i, "text": text})
        return {"path": path, "page_count": len(reader.pages), "pages_read": len(pages),
                "pages": pages, "truncated": truncated or len(pages) < len(reader.pages)}
