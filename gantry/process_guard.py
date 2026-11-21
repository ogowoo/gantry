# -*- coding: utf-8 -*-
"""子进程资源限制：Windows Job Object 内存上限 + 可强制终止。

Excel 读取放到独立子进程后，父进程可以：
- 超时直接 kill，而不是等待一个忽略取消的线程；
- 用 Job Object 给子进程设置硬内存上限（Windows）；
- POSIX 下用 resource.setrlimit(RLIMIT_AS)。

只用于本地 Excel 读取，不改变 Agent 主循环。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ProcessGuard:
    """限制一个子进程的内存；close() 时终止仍然存活的子进程。"""

    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    def __init__(self, memory_mb: int = 512):
        self.memory_mb = max(0, int(memory_mb or 0))
        self._job = None
        self._k32 = None

    def assign(self, pid: int) -> bool:
        if sys.platform != "win32" or self.memory_mb <= 0:
            return False
        try:
            self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._job = self._k32.CreateJobObjectW(None, None)
            if not self._job:
                return False
            info = _EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = (
                self.JOB_OBJECT_LIMIT_PROCESS_MEMORY
                | self.JOB_OBJECT_LIMIT_JOB_MEMORY
                | self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            info.ProcessMemoryLimit = self.memory_mb * 1024 * 1024
            info.JobMemoryLimit = self.memory_mb * 1024 * 1024
            if not self._k32.SetInformationJobObject(
                    self._job, self.JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info)):
                self.close()
                return False
            handle = self._k32.OpenProcess(
                self.PROCESS_SET_QUOTA | self.PROCESS_TERMINATE, False, int(pid))
            if not handle:
                self.close()
                return False
            try:
                ok = bool(self._k32.AssignProcessToJobObject(self._job, handle))
            finally:
                self._k32.CloseHandle(handle)
            if not ok:
                self.close()
                return False
            return True
        except Exception:
            self.close()
            return False

    def close(self) -> None:
        if self._job and self._k32:
            try:
                self._k32.CloseHandle(self._job)
            except Exception:
                pass
        self._job = None


def resident_bytes(pid: int) -> int:
    """子进程当前工作集/常驻内存（失败返回 0）。"""
    try:
        if sys.platform == "win32":
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return 0
            try:
                class _PMC(ctypes.Structure):
                    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                                ("PeakWorkingSetSize", ctypes.c_size_t),
                                ("WorkingSetSize", ctypes.c_size_t),
                                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                                ("PagefileUsage", ctypes.c_size_t),
                                ("PeakPagefileUsage", ctypes.c_size_t)]
                pmc = _PMC()
                pmc.cb = ctypes.sizeof(pmc)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc),
                                              ctypes.sizeof(pmc)):
                    return int(pmc.WorkingSetSize)
                return 0
            finally:
                k32.CloseHandle(handle)
        with open(f"/proc/{int(pid)}/statm", "r", encoding="ascii") as f:
            rss_pages = int((f.read().split() or ["0"])[1] or 0)
        return rss_pages * 4096
    except Exception:
        return 0


def posix_preexec(memory_mb: int):
    """POSIX 子进程启动前设置 RLIMIT_AS；Windows 返回 None。"""
    if sys.platform == "win32" or int(memory_mb or 0) <= 0:
        return None

    def _limit():
        try:
            import resource
            n = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (n, n))
        except Exception:
            pass

    return _limit
