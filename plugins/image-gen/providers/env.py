"""读取 API Key：进程环境 → 用户环境 → 系统环境。"""
from __future__ import annotations

import os


def env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value.strip()
    try:
        import winreg
        for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                          (winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    value, _ = winreg.QueryValueEx(key, name)
                    if value:
                        return str(value).strip()
            except OSError:
                continue
    except ImportError:
        pass
    return ""
