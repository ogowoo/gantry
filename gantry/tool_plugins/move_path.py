"""move_path tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import shutil
from typing import Any
from ..tools import ToolError


async def move_path(registry, source: str, destination: str) -> dict[str, Any]:
    src, dst = registry._path(source), registry._path(destination)
    if not src.exists():
        raise ToolError(f"源路径不存在: {source}")
    if dst == src or src in dst.parents:
        raise ToolError("禁止把目录复制/移动到它自己的子目录里")
    # 源会被移走、目标是新增内容：两端都要有原像才能回滚
    registry._snap("move_path", source, src)
    registry._snap("move_path", destination, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"source": source, "destination": destination, "moved": True}


def register(registry):
    async def _call(*args, **kwargs):
        return await move_path(registry, *args, **kwargs)
    registry.register('move_path', _call, '移动文件或目录', {"source": '相对路径', "destination": '相对路径'})
