# -*- coding: utf-8 -*-
"""Excel 读取子进程。

由 tools_office 通过 ``python -m gantry.excel_worker`` 启动；stdin/stdout 使用 JSON。
单独进程的价值：超时可以 kill，Windows Job Object 可设硬内存上限。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ok(result):
    return {"ok": True, "result": result}


def _err(exc):
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _read_range(payload: dict) -> list[list[str]]:
    import openpyxl
    path = Path(payload["path"])
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = str(payload.get("sheet") or "")
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
        rows = []
        start = max(1, int(payload.get("start_row") or 1))
        end = max(1, int(payload.get("end_row") or 1))
        max_col = max(1, int(payload.get("max_col") or 1))
        for r in ws.iter_rows(min_row=start, max_row=end, max_col=max_col,
                              values_only=True):
            vals = ["" if c is None else str(c) for c in r]
            while vals and vals[-1] == "":
                vals.pop()
            rows.append(vals)
        return rows
    finally:
        wb.close()


def _safe_bounds(g: dict) -> dict | None:
    if not g or g.get("error"):
        return None
    try:
        if int(g.get("max_row") or 0) <= 0 or int(g.get("max_col") or 0) <= 0:
            return None
        if float(g.get("confidence") or 0) < 0.15:
            return None
        return {"start_row": max(1, int(g.get("min_row") or 1)),
                "end_row": int(g["max_row"]),
                "start_col": max(1, int(g.get("min_col") or 1)),
                "end_col": int(g["max_col"])}
    except (TypeError, ValueError):
        return None


def _read_workbook(payload: dict) -> dict:
    import openpyxl
    target = Path(payload["path"])
    sheet = str(payload.get("sheet") or "")
    limit = int(payload.get("limit") or 200)
    columns = int(payload.get("columns") or 300)
    region = int(payload.get("region") or 0)
    report = payload.get("report") or {}
    guard_by = {s.get("sheet"): s for s in (report.get("sheets") or [])}
    wb = openpyxl.load_workbook(target, data_only=True, read_only=True)
    try:
        out = {}
        for name in ([sheet] if sheet else wb.sheetnames):
            ws = wb[name]
            g = guard_by.get(name) or {}
            if region and g.get("regions"):
                rs = g["regions"]
                if region <= len(rs):
                    r = rs[region - 1]
                    g = dict(g, min_row=int(r["start_row"]), max_row=int(r["end_row"]),
                             min_col=int(r.get("min_col") or g.get("min_col") or 1),
                             max_col=int(r.get("max_col") or g.get("max_col") or 1))
            guard = _safe_bounds(g)
            rows = []
            if guard:
                start_row = max(1, min(guard["start_row"], guard["end_row"]))
                end_row = min(guard["end_row"], start_row + limit - 1)
                max_col = max(1, min(columns, guard["end_col"]))
                total = max(0, guard["end_row"] - guard["start_row"] + 1)
                total_cols = max(0, guard["end_col"] - guard["start_col"] + 1)
                truncated = guard["end_row"] > end_row
            else:
                start_row, end_row, max_col = 1, limit, columns
                total = int(ws.max_row or 0)
                total_cols = int(ws.max_column or 0)
                truncated = total > limit
            for r in ws.iter_rows(min_row=start_row, max_row=end_row,
                                  max_col=max_col, values_only=True):
                vals = ["" if c is None else str(c) for c in r]
                while vals and vals[-1] == "":
                    vals.pop()
                rows.append(vals)
            out[name] = {"rows": rows, "total_rows": total,
                         "total_columns": total_cols, "columns_read": max_col,
                         "used_columns_sample": max((len(x) for x in rows), default=0),
                         "truncated": truncated,
                         "declared_range": g.get("declared_range", ""),
                         "detected_range": g.get("detected_range", ""),
                         "anomalies": g.get("anomalies") or [],
                         "confidence": g.get("confidence")}
        return {"sheets": out, "inspection": report}
    finally:
        wb.close()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_err(exc), ensure_ascii=False))
        return 0
    action = str(payload.get("action") or "")
    try:
        if action == "read_range":
            print(json.dumps(_ok({"rows": _read_range(payload)}), ensure_ascii=False))
            return 0
        if action == "read_workbook":
            print(json.dumps(_ok(_read_workbook(payload)), ensure_ascii=False))
            return 0
        if action == "inspect":
            from gantry.excel_guard import inspect_workbook
            result = inspect_workbook(
                Path(payload["path"]), str(payload.get("sheet") or ""),
                max_cells=int(payload.get("max_cells") or 200_000),
                max_rows=int(payload.get("max_rows") or 200_000),
                max_seconds=float(payload.get("max_seconds") or 8))
            print(json.dumps(_ok(result), ensure_ascii=False))
            return 0
        print(json.dumps(_err(ValueError(f"未知 action: {action}")), ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_err(exc), ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
