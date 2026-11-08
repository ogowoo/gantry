"""delete_path tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import shutil
from typing import Any
from ..tools import ToolError


async def delete_path(registry, path: str) -> dict[str, Any]:
    if not registry.allow_delete:
        raise ToolError("delete_path 已在 config.json 中禁用")
    target = registry._path(path)
    if not target.exists():
        return {"path": path, "deleted": False, "reason": "not_found"}
    if target == registry.root:
        raise ToolError("禁止删除工作区根目录")
    registry._snap("delete_path", path, target)
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    return {"path": path, "deleted": True}


def register(registry):
    async def _call(*args, **kwargs):
        return await delete_path(registry, *args, **kwargs)
    registry.register('delete_path', _call, '删除路径', {"path": '相对路径'})
