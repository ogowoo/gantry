# -*- coding: utf-8 -*-
"""temphard.py — 启动期 Temp 目录兜底：系统 Temp 拒写时改指项目内 data\\tmp。

公司策略/沙箱/EDR 会拒写 %TEMP%（症状：Playwright 起浏览器
"BrowserType.launch_persistent_context: EPERM: operation not permitted, mkdtemp
'C:\\Users\\…\\Temp\\pla…'"——浏览器永远起不来，提供商模型不受影响）。
Playwright 的 node 驱动只认 %TEMP%、无回落；Python 的 tempfile 探测失败却会
静默回落 cwd——所以探测必须直接对 node 将要使用的目录本身试建子目录。
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # 包在 gantry/ 内，项目根再上一层


def ensure_writable_temp(base: Path | None = None) -> Path | None:
    """探测系统 Temp 可写性；不可写则把 TEMP/TMP/TMPDIR 改指 <base>/data/tmp。

    返回改指后的目录（已改指），或 None（系统 Temp 可用/备用目录也不可用）。
    探测失败才改指，正常机器零开销、零行为变化。
    """
    cand = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    try:
        probe = Path(cand) / f"gantry_probe_{os.getpid()}_{time.time_ns()}"
        probe.mkdir()          # parents=False：基目录缺失也算不可写（node 同样会失败）
        probe.rmdir()
        return None
    except Exception:
        pass
    tmp = Path(base or BASE) / "data" / "tmp"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        probe = tmp / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception:
        return None             # 备用也不可用：保持现状，让上层报真实错误
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(tmp)
    tempfile.tempdir = None     # Python 侧缓存清掉，gettempdir() 按新环境重算
    return tmp
