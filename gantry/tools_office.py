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
import os
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

import logging as _logging

_LOG = _logging.getLogger(__name__)

# 写路径串行化：write_csv/write_excel 并发写同一文件会产生交错行/截断，
# 统一在模块级锁内完成（append 模式读-改-写也必须整体原子）。
# AUD-P0-02：必须用 asyncio.Lock——threading.Lock 在 async 函数里跨 await 持有时，
# 等待方会阻塞事件循环线程，与持锁方形成互等，整个服务永久卡死（审核 ⑥-1）。
_WRITE_LOCK = asyncio.Lock()


def _looks_like_valid_text(text: str) -> bool:
    """解码结果启发校验：NUL 或 U+FFFD 比例异常即视为解码失败。

    gb18030/GBK 能"成功"解码几乎所有字节序列，单看是否抛异常会把
    UTF-8 日文/韩文文件解成乱码；NUL 字符基本只出现在二进制文件，
    U+FFFD 只可能来自错误处理（这里 decode 用 strict，正常不会产生，
    调用方传入带替换符的文本也能被拦住）。
    """
    if not text:
        return True
    if "\x00" in text:
        return False
    n_repl = text.count("\ufffd")
    if n_repl and n_repl / len(text) > 0.001:
        return False
    return True


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


class _WorkerDead(Exception):
    """常驻 worker 进程死亡/管道断开，调用方应重启后重试一次。"""


class _PersistentExcelWorker:
    """常驻 excel_worker 子进程：请求走 stdin/stdout 行协议，免每调用 spawn。

    Windows 上 python 子进程 spawn 开销实测在 0.2~1.2s 间波动（杀软干扰），
    分块循环读时这是每块都付的固定税；常驻进程把每请求开销降到毫秒级。
    请求间无状态、单线程串行（调用侧用 threading.Lock 保证），
    空闲超过 _MAX_IDLE 秒自动回收（连同 Job Object 内存上限一起释放）。
    """

    _MAX_IDLE = 300.0

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._guard = None
        self._idle_since = time.monotonic()

    def request(self, payload: dict[str, Any], timeout: float,
                limit_mb: int, root: Path) -> dict[str, Any]:
        with self._lock:
            self._reap_if_idle()
            try:
                return self._request_once(payload, timeout, limit_mb, root)
            except _WorkerDead:
                # 崩溃/被杀（如超时 watchdog）：重启一次并重试；再死就是
                # 真问题（如文件超内存上限反复 OOM），转 ToolError 带上文。
                self._stop()
                try:
                    return self._request_once(payload, timeout, limit_mb, root)
                except _WorkerDead as exc:
                    raise ToolError(
                        f"Excel 子进程反复崩溃（已重启重试一次）: {exc}；"
                        f"文件可能超过内存上限 {limit_mb} MB")

    def _request_once(self, payload: dict[str, Any], timeout: float,
                      limit_mb: int, root: Path) -> dict[str, Any]:
        # 亚 50ms 的超时对「进程间 RPC」没有意义（一次往返本身就可能在毫秒级，
        # 判定会随时序抖动变成随机结果）：配置过小直接按超时处理，语义确定。
        if timeout < 0.05:
            raise ToolError(
                f"Excel 超时配置过小（{timeout}s），最小 0.05 秒；请调大或缩小读取范围")
        proc = self._ensure(limit_mb, root)
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        # 写 stdin 放独立线程：请求/响应都可能超过管道缓冲（64KB），
        # 单线程先写后读会在两端同时填满管道时死锁。
        write_err: list[BaseException] = []
        state = {"timed_out": False}

        def _write():
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                write_err.append(exc)

        def _watch():
            if not done.wait(timeout):
                state["timed_out"] = True
                try:
                    proc.kill()
                except Exception:
                    pass

        done = threading.Event()
        writer = threading.Thread(target=_write, daemon=True)
        watchdog = threading.Thread(target=_watch, daemon=True)
        writer.start()
        watchdog.start()
        started = time.monotonic()
        try:
            out = proc.stdout.readline()
        finally:
            done.set()
        # 超时判定放在主线程：watchdog 线程的唤醒精度受 GIL/调度影响，
        # 暖 worker 快速应答时可能抢不过；这里按契约严格判“超过即终止”。
        if out and time.monotonic() - started > timeout:
            try:
                proc.kill()
            except Exception:
                pass
            raise ToolError(
                f"Excel 子进程超过 {int(timeout)} 秒，已强制终止；请缩小范围")
        writer.join(timeout=5)
        if write_err:
            raise _WorkerDead(str(write_err[0]))
        if not out:
            if state["timed_out"]:
                raise ToolError(
                    f"Excel 子进程超过 {int(timeout)} 秒，已强制终止；请缩小范围")
            raise _WorkerDead(f"worker exited (code {proc.poll()})")
        self._idle_since = time.monotonic()
        try:
            return json.loads(out.decode("utf-8", "ignore"))
        except Exception as exc:
            raise _WorkerDead(f"worker 返回无效 JSON: {exc}")

    def _ensure(self, limit_mb: int, root: Path) -> subprocess.Popen:
        p = self._proc
        if p is not None and p.poll() is None:
            return p
        return self._start(limit_mb, root)

    def _start(self, limit_mb: int, root: Path) -> subprocess.Popen:
        self._stop()
        from .process_guard import ProcessGuard
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + (os.pathsep + env["PYTHONPATH"]
                                         if env.get("PYTHONPATH") else "")
        # stderr 走 DEVNULL：错误本来就以 {"ok": false} 在带内返回，
        # 不 drain 的 stderr 管道反而可能撑满阻塞子进程。
        proc = subprocess.Popen(
            [sys.executable, "-m", "gantry.excel_worker", "serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(root), env=env)
        self._guard = ProcessGuard(limit_mb)
        if not self._guard.assign(proc.pid):
            # Job Object 不可用时内存上限与"关句柄连带终止"都失效，
            # _stop() 会退化到 proc.kill() 兜底；这里记日志便于排查。
            _LOG.warning(
                "Excel worker Job Object 不可用（pid=%s，limit=%sMB），"
                "内存上限失效，退化为 kill 兜底", proc.pid, limit_mb)
        self._proc = proc
        return proc

    def _reap_if_idle(self):
        if (self._proc is not None
                and time.monotonic() - self._idle_since > self._MAX_IDLE):
            self._stop()

    def _stop(self):
        p = self._proc
        self._proc = None
        guarded = self._guard is not None
        if self._guard is not None:
            try:
                self._guard.close()  # KILL_ON_JOB_CLOSE：连带终止子进程
            except Exception:
                guarded = False
            self._guard = None
        if p is not None:
            for stream in (p.stdin, p.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            if guarded:
                try:
                    p.wait(timeout=5)  # Job close 已发终止，等它退出
                except Exception:
                    guarded = False
            if not guarded:
                # Job Object 不可用（guard 为 None 或 assign 失败）时
                # close() 不会终止子进程，必须显式 kill，否则会泄漏。
                try:
                    p.kill()
                except Exception:
                    pass
                try:
                    p.wait(timeout=5)
                except Exception:
                    pass


_WORKER_POOL = _PersistentExcelWorker()
import atexit as _atexit  # noqa: E402
_atexit.register(_WORKER_POOL._stop)


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

    async def _inspect_report(self, target: Path, sheet: str, max_cells: int,
                              max_rows: int, max_seconds: float) -> dict[str, Any]:
        """excel_guard 探测 + 按 (路径, 大小, mtime, 参数) 缓存。

        excel_preview/excel_read_chunk/read_excel 每次调用都先全量探测一遍
        （大表 1~2 秒），分块循环读时这是每块都付一次的固定开销；
        文件指纹没变结果必然相同，缓存后直接省掉。键含 mtime_ns，
        文件被改写（含同秒覆盖）必然换键，不会脏读。
        """
        cache = getattr(self, "_excel_inspect_cache", None)
        if cache is None:
            cache = self._excel_inspect_cache = {}
        key = None
        try:
            st = target.stat()
            key = (str(target.resolve()), st.st_size, st.st_mtime_ns, st.st_ino,
                   sheet, max_cells, max_rows, max_seconds)
        except OSError:
            pass
        if key is not None and key in cache:
            return cache[key]
        report = await self._excel_worker(
            {"action": "inspect", "path": str(target), "sheet": sheet,
             "max_cells": max_cells, "max_rows": max_rows,
             "max_seconds": max_seconds}, max_seconds + 10)
        if key is not None:
            if len(cache) >= 8:
                cache.pop(next(iter(cache)))
            cache[key] = report
        return report

    @staticmethod
    def _legacy_xls_supported() -> bool:
        """python-calamine 可用时 .xls/.xlsb/.ods 也能读（openpyxl 不支持）。"""
        try:
            import python_calamine  # noqa: F401
            return True
        except ImportError:
            return False

    def _reject_legacy_xls(self, target: Path):
        if target.suffix.lower() == ".xls" and not self._legacy_xls_supported():
            raise ToolError("旧版 .xls 需要 python-calamine；请先 pip install "
                            "python-calamine，或另存为 .xlsx/.xlsm")

    async def _excel_worker(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        """在独立子进程里跑 excel_worker；超时可 kill，Job Object 限制内存。

        用同步 subprocess 放线程里执行而非 asyncio 子进程：Windows 上
        ProactorEventLoop 的 asyncio 子进程每次 spawn 有 ~1 秒固定开销
        （实测 1.2s vs 同步 0.17s），分块循环读时被放大成主要瓶颈。
        """
        return await asyncio.to_thread(self._excel_worker_sync, payload, timeout)

    def _excel_worker_sync(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if not bool(self.config.get("excel_use_worker", True)):
            raise RuntimeError("excel worker disabled")
        root = Path(__file__).resolve().parent.parent
        limit_mb = int(self.config.get("excel_worker_memory_mb", 512) or 512)
        data = _WORKER_POOL.request(payload, timeout, limit_mb, root)
        if not data.get("ok"):
            raise ToolError(str(data.get("error") or "Excel 子进程失败"))
        return data.get("result") or {}

    async def excel_preview(self, path: str, sheet: str = "",
                            max_rows: int = 5) -> dict[str, Any]:
        """Excel 预览：真实范围 + 表头 + 前几行 + 数据区域。"""
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        self._reject_legacy_xls(target)
        limit = max(1, min(self._int_arg(max_rows, 5, "max_rows"), 30))
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        if target.suffix.lower() == ".xls":
            # 非 zip 格式无法走 excel_guard 探测；calamine 直接读前几行做预览。
            data = await self._excel_worker(
                {"action": "read_workbook", "path": str(target), "sheet": sheet,
                 "limit": limit, "columns": 1000, "report": {}, "region": 0}, timeout)
            sheets = data.get("sheets") or {}
            name = sheet or (next(iter(sheets), ""))
            picked_sheet = sheets.get(name) or {}
            return {"path": path, "sheet": name,
                    "rows": picked_sheet.get("rows") or [],
                    "total_rows": picked_sheet.get("total_rows", 0),
                    "legacy_format": True,
                    "warnings": ["xls 无结构探测，仅预览前 N 行"]}
        if bool(self.config.get("excel_use_worker", True)):
            report = await self._inspect_report(
                target, sheet, 200_000, 200_000, min(8.0, timeout))
        else:
            report = await asyncio.to_thread(_inspect_for_read, target, sheet)
        from .excel_guard import recommended_scan
        guard = recommended_scan(report, sheet)
        if not guard or not guard.get("end_row"):
            return {"path": path, "inspection": report, "rows": [],
                    "header": [], "regions": [], "warnings": ["没有识别到数据区域"]}
        end = min(guard["end_row"], guard["start_row"] + limit - 1)
        try:
            if bool(self.config.get("excel_use_worker", True)):
                data = await self._excel_worker(
                    {"action": "read_range", "path": str(target),
                     "sheet": guard["sheet"], "start_row": guard["start_row"],
                     "end_row": end, "max_col": min(1000, guard["end_col"]),
                     "max_cols": 1000}, timeout)
                rows = data.get("rows") or []
            else:
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
            raise ToolError("excel_read_chunk 只支持 .xlsx/.xlsm（需要结构探测）；"
                            ".xls 请用 read_excel 整读，或先另存为 .xlsx")
        size = max(1, min(self._int_arg(chunk_rows, 500, "chunk_rows"), 2000))
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        if bool(self.config.get("excel_use_worker", True)):
            report = await self._inspect_report(
                target, sheet, 200_000, 200_000, min(8.0, timeout))
        else:
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
        try:
            if bool(self.config.get("excel_use_worker", True)):
                data = await self._excel_worker(
                    {"action": "read_range", "path": str(target),
                     "sheet": guard["sheet"], "start_row": start, "end_row": end,
                     "max_col": min(1000, guard["end_col"])}, timeout)
                rows = data.get("rows") or []
            else:
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
            fingerprint = f"{st.st_size}:{st.st_mtime_ns}:{st.st_ino}"
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
        self._reject_legacy_xls(target)
        limit = max(1, min(self._int_arg(max_rows, 200, "max_rows"), 2000))
        columns = max(1, min(self._int_arg(max_cols, 300, "max_cols"), 1000))
        try:
            region_n = max(0, int(region or 0))
        except (TypeError, ValueError):
            raise ToolError("region 必须是整数")
        report = None
        timeout = float(self.config.get("excel_read_timeout_seconds", 60) or 60)
        use_worker = bool(self.config.get("excel_use_worker", True))
        if safe:
            if use_worker:
                report = await self._inspect_report(
                    target, sheet, 200_000, 200_000, min(8.0, timeout))
            else:
                report = await asyncio.to_thread(_inspect_for_read, target, sheet)
            if region_n:
                picked = next((s for s in (report.get("sheets") or [])
                               if not sheet or s.get("sheet") == sheet), None)
                regions = (picked or {}).get("regions") or []
                if region_n > len(regions):
                    raise ToolError(f"region={region_n} 超出范围，可用区域数: {len(regions)}")
        try:
            if use_worker:
                out = await self._excel_worker(
                    {"action": "read_workbook", "path": str(target),
                     "sheet": sheet, "limit": limit, "columns": columns,
                     "report": report, "region": region_n}, timeout)
            else:
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
        report = (await self._excel_worker(
            {"action": "inspect", "path": str(target), "sheet": sheet,
             "max_cells": budget, "max_rows": rows_budget,
             "max_seconds": secs}, secs + 10)
            if bool(self.config.get("excel_use_worker", True))
            else await asyncio.to_thread(
                inspect_workbook, target, sheet, max_cells=budget,
                max_rows=rows_budget, max_seconds=secs))
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
        if bool(self.config.get("excel_use_worker", True)):
            report = await self._excel_worker(
                {"action": "inspect", "path": str(target), "sheet": sheet,
                 "max_cells": 200_000, "max_rows": 200_000,
                 "max_seconds": 8.0}, 30)
        else:
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
        # 写锁内完成：并发 write_excel 同一文件会把 zip 写坏。
        async with _WRITE_LOCK:
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

    @staticmethod
    def _pptx_shape_texts(shapes) -> tuple[list[str], list[str]]:
        """递归提取文本框与表格（含组合形状里的）。

        原来的实现只看 slide.shapes 一层的 has_text_frame，漏掉两类常见内容：
        图形框架里的表格（shape.has_table）和组合形状（group）内部的任何东西。
        """
        texts: list[str] = []
        tables: list[str] = []
        for shape in shapes:
            if getattr(shape, "shapes", None) is not None:  # 组合形状
                t, tb = OfficeMixin._pptx_shape_texts(shape.shapes)
                texts.extend(t)
                tables.extend(tb)
                continue
            if shape.has_text_frame and shape.text.strip():
                texts.append(shape.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    tables.append(" | ".join(
                        cell.text.strip() for cell in row.cells))
        return texts, tables

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
            texts, tables = self._pptx_shape_texts(slide.shapes)
            notes = ""
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
                notes = slide.notes_slide.notes_text_frame.text.strip()
            slides.append({"slide": i, "text": "\n".join(texts), "notes": notes,
                           "tables": tables})
        total_chars = sum(len(s["text"]) + len(s["notes"]) for s in slides)
        return {"path": path, "slides": slides, "slide_count": len(slides),
                "truncated": total_chars > self.max_read}

    async def read_csv(self, path: str, max_rows: int = 2000,
                       max_cols: int = 200, encoding: str = "") -> dict[str, Any]:
        """读取 CSV 文件。encoding 默认自动：utf-8-sig → utf-8 → gb18030。

        gb18030 能解几乎所有字节序列，解码"成功"不代表内容正确——这里做
        启发校验：含 NUL 或 U+FFFD 替换符比例异常即视为解码失败，不报乱码。
        """
        import csv
        self._office_allowed()
        target = self._path(path)
        if not target.is_file():
            raise ToolError(f"文件不存在: {path}")
        limit = max(1, min(self._int_arg(max_rows, 2000, "max_rows"), 50000))
        cols = max(1, min(self._int_arg(max_cols, 200, "max_cols"), 1000))
        # csv 模块默认 field_size_limit 128KB，长文本/大字段行直接抛
        # _csv.Error("field larger than field limit")；放宽到 10MB 级别。
        try:
            csv.field_size_limit(min(10 * 1024 * 1024, sys.maxsize))
        except (OverflowError, ValueError):
            csv.field_size_limit(sys.maxsize)
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ToolError(f"无法读取 CSV 文件: {exc}")
        encodings = ([encoding] if encoding else []) + ["utf-8-sig", "utf-8", "gb18030"]
        last_err = None
        for enc in encodings:
            try:
                text = data.decode(enc)
            except LookupError:
                raise ToolError(f"未知编码: {enc}")
            except (UnicodeDecodeError, UnicodeError) as exc:
                last_err = exc
                continue
            if not _looks_like_valid_text(text):
                # gb18030 兜底把非 GBK 字节解成了乱码（NUL/替换符），
                # 当作解码失败继续尝试，而不是把乱码交回给调用方。
                last_err = UnicodeDecodeError(
                    enc, data, 0, min(len(data), 16),
                    "解码结果含 NUL 或过多 U+FFFD，疑似编码不匹配")
                continue
            rows: list[list[str]] = []
            truncated = False
            try:
                import io
                for i, row in enumerate(csv.reader(io.StringIO(text, newline=""))):
                    if i >= limit:
                        truncated = True
                        break
                    rows.append([("" if v is None else str(v)) for v in row[:cols]])
            except csv.Error as exc:
                raise ToolError(f"CSV 解析失败: {exc}")
            return {"path": path, "encoding": enc, "rows": rows,
                    "rows_read": len(rows), "truncated": truncated}
        raise ToolError(f"无法解码 CSV（尝试了 {encodings}）: {last_err}")

    async def write_csv(self, path: str, rows: list, mode: str = "overwrite",
                        encoding: str = "utf-8-sig",
                        escape_formulas: bool = True) -> dict[str, Any]:
        """写入 CSV（二维数组）。mode=append 追加；默认覆盖。utf-8-sig 带 BOM，Excel 直接双击不乱码。

        escape_formulas=True（默认）时，以 =,+,-,@ 开头的文本单元格前导加 '，
        防止 CSV 公式注入（Excel 打开即执行）；纯数字负数不受影响（非 str 值）。
        全程在模块级写锁内 tmp+os.replace 原子落盘。
        """
        self._office_allowed()
        if not isinstance(rows, list) or not rows:
            raise ToolError("rows 必须是非空二维数组")
        if mode not in ("append", "overwrite"):
            raise ToolError("mode 只能是 append 或 overwrite")
        import csv
        import io
        target = self._path(path)
        self._snap("write_csv", path, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = self._hash(target)
        enc = encoding or "utf-8-sig"
        async with _WRITE_LOCK:
            if mode == "append" and target.is_file():
                # append 无法原地原子：读旧内容 + 拼接 + 整盘替换，
                # 在锁内完成，对其他写者仍表现为原子。
                old = target.read_bytes()
            else:
                old = b""
            buf = io.StringIO(newline="")
            # 追加时保留原字节（含 BOM），新行直接续写；覆盖时 csv.writer
            # 用 utf-8-sig 编码器会自动补 BOM。
            buf.write(old.decode(enc, "ignore") if old else "")
            writer = csv.writer(buf)
            for row in rows:
                cells = row if isinstance(row, list) else [row]
                if escape_formulas:
                    cells = [("'" + v if isinstance(v, str)
                              and v[:1] in ("=", "+", "-", "@") else v)
                             for v in cells]
                writer.writerow(cells)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(buf.getvalue().encode(enc))
            os.replace(tmp, target)
        return {"path": path, "mode": mode, "rows_written": len(rows),
                "file_event": {"tool": "write_csv", "path": path,
                               "before_hash": before, "after_hash": self._hash(target),
                               "result": "ok"}}

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
