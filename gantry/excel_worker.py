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


def _cell_str(v) -> str:
    """统一单元格显示值，保证与 openpyxl 路径输出一致。

    calamine 的数字统一是 float（2.0），openpyxl 会给整数值（2）；
    bool 两边都转 "True"/"False"；None→""。
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _calamine_sheet(path: Path, sheet: str, wb=None):
    """calamine 可用且能打开该文件时返回 sheet 对象，否则返回 None（走 openpyxl）。

    python-calamine 是 Rust 实现，读大表比 openpyxl 快一个数量级，
    且原生支持 .xls/.xlsb/.ods（openpyxl 不支持）。
    wb 为 None 时自行 from_path；调用方应每请求只 from_path 一次并传入。
    """
    wb = _calamine_workbook(path) if wb is None else wb
    if wb is None:
        return None
    try:
        names = list(wb.sheet_names)
        if sheet:
            if sheet not in names:
                raise KeyError(f"工作表不存在: {sheet}，可用: {names}")
            return wb.get_sheet_by_name(sheet)
        return wb.get_sheet_by_index(0)
    except KeyError:
        raise
    except Exception:
        return None


def _rows_trim(vals: list[str]) -> list[str]:
    while vals and vals[-1] == "":
        vals.pop()
    return vals


def _read_range(payload: dict) -> list[list[str]]:
    path = Path(payload["path"])
    sheet = str(payload.get("sheet") or "")
    start = max(1, int(payload.get("start_row") or 1))
    end = max(start, int(payload.get("end_row") or start))
    max_col = max(1, int(payload.get("max_col") or 1))
    cal = _calamine_workbook(path)
    if cal is not None:
        try:
            _, rows = _sheet_rows(path, sheet, cal)
        except KeyError:
            raise
        if rows is not None:
            return [_rows_trim([_cell_str(v) for v in row[:max_col]])
                    for row in rows[start - 1:end]]
        sh = _calamine_sheet(path, sheet, cal)
        if sh is not None:
            out = []
            for i, row in enumerate(sh.iter_rows(), start=1):
                if i > end:
                    break
                if i < start:
                    continue
                out.append(_rows_trim([_cell_str(v) for v in row[:max_col]]))
            return out
    wb = _openpyxl_read(path, data_only=True, read_only=True)
    try:
        ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.active
        rows = []
        for r in ws.iter_rows(min_row=start, max_row=end, max_col=max_col,
                              values_only=True):
            vals = ["" if c is None else str(c) for c in r]
            rows.append(_rows_trim(vals))
        return rows
    finally:
        wb.close()


def _openpyxl_read(path: Path, **kw):
    """openpyxl 只支持 xlsx/xlsm；旧格式解析失败时给自家文案而非 openpyxl 报错。"""
    import openpyxl
    try:
        return openpyxl.load_workbook(path, **kw)
    except Exception as exc:
        if path.suffix.lower() in (".xls", ".xlsb", ".ods"):
            raise ValueError(
                f"无法读取旧版 {path.suffix} 文件（内容损坏或缺少解析支持，"
                f"可尝试 pip install python-calamine）: {exc}") from exc
        raise


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


def _bounds(g: dict, limit: int, columns: int) -> dict:
    guard = _safe_bounds(g)
    if guard:
        start_row = max(1, min(guard["start_row"], guard["end_row"]))
        end_row = min(guard["end_row"], start_row + limit - 1)
        max_col = max(1, min(columns, guard["end_col"]))
        total = max(0, guard["end_row"] - guard["start_row"] + 1)
        total_cols = max(0, guard["end_col"] - guard["start_col"] + 1)
        truncated = guard["end_row"] > end_row
    else:
        start_row, end_row, max_col = 1, limit, columns
        total = total_cols = truncated = 0  # 由具体引擎补 max_row/max_column
    return {"start_row": start_row, "end_row": end_row, "max_col": max_col,
            "total": total, "total_cols": total_cols, "truncated": truncated}


def _read_workbook(payload: dict) -> dict:
    path = Path(payload["path"])
    sheet = str(payload.get("sheet") or "")
    limit = int(payload.get("limit") or 200)
    columns = int(payload.get("columns") or 300)
    region = int(payload.get("region") or 0)
    report = payload.get("report") or {}
    guard_by = {s.get("sheet"): s for s in (report.get("sheets") or [])}

    def _guard_for(name: str) -> dict:
        g = guard_by.get(name) or {}
        if region and g.get("regions"):
            rs = g["regions"]
            if region <= len(rs):
                r = rs[region - 1]
                g = dict(g, min_row=int(r["start_row"]), max_row=int(r["end_row"]),
                         min_col=int(r.get("min_col") or g.get("min_col") or 1),
                         max_col=int(r.get("max_col") or g.get("max_col") or 1))
        return g

    def _sheet_out(g: dict, rows: list[list[str]], ws_max_row: int,
                   ws_max_col: int) -> dict:
        b = _bounds(g, limit, columns)
        if not b["total"]:
            b["total"] = int(ws_max_row or 0)
            b["total_cols"] = int(ws_max_col or 0)
            b["truncated"] = b["total"] > limit
        return {"rows": rows, "total_rows": b["total"],
                "total_columns": b["total_cols"], "columns_read": b["max_col"],
                "used_columns_sample": max((len(x) for x in rows), default=0),
                "truncated": b["truncated"],
                "declared_range": g.get("declared_range", ""),
                "detected_range": g.get("detected_range", ""),
                "anomalies": g.get("anomalies") or [],
                "confidence": g.get("confidence")}

    cal = _calamine_workbook(path)
    if cal is not None:
        names = [sheet] if sheet else list(cal.sheet_names)
        out = {}
        for name in names:
            try:
                sh, all_rows = _sheet_rows(path, name, cal)
            except KeyError:
                raise KeyError(f"工作表不存在: {name}，可用: {list(cal.sheet_names)}")
            g = _guard_for(name)
            if all_rows is not None:
                total_height, total_width = len(all_rows), \
                    max((len(r) for r in all_rows), default=0)
                b = _bounds(g, limit, columns)
                rows = [_rows_trim([_cell_str(v) for v in row[:b["max_col"]]])
                        for row in all_rows[b["start_row"] - 1:b["end_row"]]]
            else:
                if sh is None:
                    raise KeyError(f"工作表不存在: {name}，可用: {list(cal.sheet_names)}")
                total_height, total_width = sh.total_height, sh.total_width
                b = _bounds(g, limit, columns)
                rows = []
                for i, row in enumerate(sh.iter_rows(), start=1):
                    if i > b["end_row"]:
                        break
                    if i < b["start_row"]:
                        continue
                    rows.append(_rows_trim([_cell_str(v) for v in row[:b["max_col"]]]))
            out[name] = _sheet_out(g, rows, total_height, total_width)
        return {"sheets": out, "inspection": report}

    wb = _openpyxl_read(path, data_only=True, read_only=True)
    try:
        if sheet and sheet not in wb.sheetnames:
            raise KeyError(f"工作表不存在: {sheet}，可用: {wb.sheetnames}")
        out = {}
        for name in ([sheet] if sheet else wb.sheetnames):
            ws = wb[name]
            g = _guard_for(name)
            b = _bounds(g, limit, columns)
            rows = []
            for r in ws.iter_rows(min_row=b["start_row"], max_row=b["end_row"],
                                  max_col=b["max_col"], values_only=True):
                vals = ["" if c is None else str(c) for c in r]
                rows.append(_rows_trim(vals))
            out[name] = _sheet_out(g, rows, ws.max_row, ws.max_column)
        return {"sheets": out, "inspection": report}
    finally:
        wb.close()


def _calamine_workbook(path: Path):
    """calamine 能打开则返回 workbook，否则 None。

    注意：calamine 的解析是惰性的且 get_sheet_by_name 每次都重新解析，
    缓存 CalamineWorkbook 对象本身没有用——要省重复解析，须在更上层缓存
    物化后的行（见 _sheet_rows）。
    """
    try:
        import python_calamine
    except ImportError:
        return None
    try:
        return python_calamine.CalamineWorkbook.from_path(str(path))
    except Exception:
        return None


_ROWS_CACHE: dict = {}  # (file_key, sheet) -> (rows, cell_count)
_ROWS_CACHE_MAX_CELLS = 1_200_000   # 单个表缓存上限（~1M 单元格 ≈ 200MB 对象）
_ROWS_CACHE_TOTAL_CELLS = 1_500_000  # 全部缓存条目的总预算（512MB Job 上限内）
_ROWS_CACHE_MAX_ENTRIES = 2


def _file_key(path: Path):
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_size, st.st_mtime_ns, st.st_ino)
    except OSError:
        return None


def _sheet_rows(path: Path, sheet: str, wb=None):
    """返回 (sheet对象, 物化行或 None)。

    物化行缓存（按 文件指纹+表名）：分块循环读时首个请求付一次
    解析+物化，后续块纯切片毫秒级返回。超过单元格预算的大表不缓存
    （worker 内存有限，靠 Job Object 上限兜底），调用方走 iter_rows 流式。
    wb 为 None 时自行 from_path；调用方应每请求只 from_path 一次并传入。
    """
    wb = _calamine_workbook(path) if wb is None else wb
    if wb is None:
        return None, None
    names = list(wb.sheet_names)
    name = sheet or names[0]
    if name not in names:
        raise KeyError(f"工作表不存在: {sheet}，可用: {names}")
    fkey = _file_key(path)
    ckey = (fkey, name) if fkey is not None else None
    if ckey is not None and ckey in _ROWS_CACHE:
        return None, _ROWS_CACHE[ckey][0]
    try:
        sh = wb.get_sheet_by_name(name)
        rows = sh.to_python()
    except Exception:
        return None, None
    cells = sum(len(r) for r in rows)
    if ckey is not None and cells <= _ROWS_CACHE_MAX_CELLS:
        total = sum(v[1] for v in _ROWS_CACHE.values()) + cells
        while (_ROWS_CACHE and (len(_ROWS_CACHE) >= _ROWS_CACHE_MAX_ENTRIES
                                or total > _ROWS_CACHE_TOTAL_CELLS)):
            total -= _ROWS_CACHE.pop(next(iter(_ROWS_CACHE)))[1]
        _ROWS_CACHE[ckey] = (rows, cells)
    return sh, rows


def _dispatch(payload: dict) -> dict:
    action = str(payload.get("action") or "")
    if action == "read_range":
        return _ok({"rows": _read_range(payload)})
    if action == "read_workbook":
        return _ok(_read_workbook(payload))
    if action == "inspect":
        from gantry.excel_guard import inspect_workbook
        result = inspect_workbook(
            Path(payload["path"]), str(payload.get("sheet") or ""),
            max_cells=int(payload.get("max_cells") or 200_000),
            max_rows=int(payload.get("max_rows") or 200_000),
            max_seconds=float(payload.get("max_seconds") or 8))
        return _ok(result)
    return _err(ValueError(f"未知 action: {action}"))


def serve() -> int:
    """常驻模式：stdin 每行一个 JSON 请求，stdout 每行一个 JSON 响应。

    避免每次调用重新 spawn python + 加载 calamine（Windows 上进程
    spawn 受杀软影响波动在 0.2~1.2s）；请求间无状态，单线程逐行处理。
    """
    # 父进程写 UTF-8 字节到 stdin；这里按二进制迭代再显式 decode，
    # 不依赖 locale（Windows 上 sys.stdin 默认 cp936/cp1252 会乱码）。
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            print(json.dumps(_dispatch(payload), ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps(_err(exc), ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        return serve()
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_err(exc), ensure_ascii=False))
        return 0
    try:
        print(json.dumps(_dispatch(payload), ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(_err(exc), ensure_ascii=False))
        return 0


if __name__ == "__main__":
    sys.exit(main())
