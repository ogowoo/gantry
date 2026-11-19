"""Gantry MCP stdio 启动器：把 gimp-mcp 直接 exec 到 Python 子进程。

gimp-mcp 已由 uvx 安装；但 Gantry MCP 插件通过 `uvx.exe` 作为 stdio server 时，
uv 包装器会在 Windows 上多一层进程并让 tools/list 超时。这里用插件允许的
`python.exe` 作为入口，exec 到缓存里的 gimp-mcp.exe，stdin/stdout 直通。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _find() -> str:
    env = os.environ.get("GIMP_MCP_EXE", "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("gimp-mcp")
    if found:
        return found
    cache = Path.home() / "AppData" / "Local" / "uv" / "cache" / "archive-v0"
    candidates = sorted(cache.glob("*/Scripts/gimp-mcp.exe"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return str(candidates[0])
    candidates = sorted(cache.glob("*/Scripts/gimp-mcp"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return str(candidates[0])
    raise SystemExit("找不到 gimp-mcp；先运行 uvx gimp-mcp --version")


def main() -> None:
    exe = _find()
    args = [exe, "serve", *sys.argv[1:]]
    # Windows 没有真正的 execv；Popen 继承 stdio 也能直通，但会多一个 Python
    # 父进程。用 os.execv 让 gimp-mcp 接管当前进程，最接近 uvx 的行为。
    try:
        os.execv(exe, args)
    except OSError:
        import subprocess
        raise SystemExit(subprocess.call(args))


if __name__ == "__main__":
    main()
