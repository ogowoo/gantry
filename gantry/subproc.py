# -*- coding: utf-8 -*-
"""跨平台隐藏子进程控制台窗口。

Windows 上从 pythonw / DETACHED_PROCESS 的服务里启动 `python -m unittest`、
`git`、`powershell` 等控制台程序时，系统会新建一个黑色控制台窗口并抢焦点。
统一使用 `CREATE_NO_WINDOW`；POSIX 返回空参数，不影响原有行为。
"""
from __future__ import annotations

import os
import subprocess


def hidden_kwargs() -> dict:
    """给 subprocess.run / asyncio.create_subprocess_exec 的隐藏窗口参数。"""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}
