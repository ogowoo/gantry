"""make_directory tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError


async def make_directory(registry, path: str) -> dict[str, Any]:
    target = registry._path(path)
    if not target.exists():
        registry._snap("make_directory", path, target)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": path, "created": True}


def register(registry):
    async def _call(*args, **kwargs):
        return await make_directory(registry, *args, **kwargs)
    registry.register('make_directory', _call, '创建目录', {"path": '相对路径'})
