"""copy_path tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import shutil
from typing import Any
from ..tools import ToolError


async def copy_path(registry, source: str, destination: str) -> dict[str, Any]:
    src, dst = registry._path(source), registry._path(destination)
    if not src.exists():
        raise ToolError(f"源路径不存在: {source}")
    if dst == src or src in dst.parents:
        raise ToolError("禁止把目录复制/移动到它自己的子目录里")
    registry._snap("copy_path", destination, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return {"source": source, "destination": destination, "copied": True}


def register(registry):
    async def _call(*args, **kwargs):
        return await copy_path(registry, *args, **kwargs)
    registry.register('copy_path', _call, '复制文件或目录', {"source": '相对路径', "destination": '相对路径'})
