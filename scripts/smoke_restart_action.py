# -*- coding: utf-8 -*-
"""端到端验证托盘的「重启服务」动作：真的重启 + 等 /health 回来。

这个脚本直接调用 Tray._a_restart()（不建图标，balloon 在没有 shell32 时会静默跳过），
所以走的是**托盘菜单点下去**的同一条代码路径。
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from gantry import tray as tray_mod  # noqa: E402

runs: list[tuple[str, str, bool]] = []
t = tray_mod.Tray(port=8765, root=Path(".").resolve(), lang="zh")
t.balloon = lambda title, text, error=False: runs.append((title, text, error))  # type: ignore
orig = tray_mod.http_json

calls: list[str] = []


def spy(url, payload=None, timeout=8.0):
    calls.append(url)
    return orig(url, payload, timeout)


tray_mod.http_json = spy
t0 = time.time()
t._a_restart()
elapsed = time.time() - t0
tray_mod.http_json = orig

print(f"耗时 {elapsed:.1f}s | http 调用 {len(calls)} 次（第一个应是 /v1/restart）")
print("首个调用:", calls[0] if calls else "无")
print("气泡:", runs)
ok = (calls and calls[0].endswith("/v1/restart")
      and any("/health" in c for c in calls)
      and runs and runs[-1][2] is False)
print("结论:", "重启动作走通并且是等到 /health 才报成功" if ok else "⚠ 不符合预期")
sys.exit(0 if ok else 1)
