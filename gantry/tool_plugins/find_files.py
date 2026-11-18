"""find_files tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError


async def find_files(registry, pattern: str) -> dict[str, Any]:
    if not pattern:
        raise ToolError("pattern 不能为空")
    matches = []
    for item in registry.root.glob(pattern):
        try:
            rel = item.resolve().relative_to(registry.root)
        except ValueError:
            continue
        matches.append(str(rel))
        if len(matches) >= 1000:
            break
    return {"pattern": pattern, "matches": matches, "truncated": len(matches) >= 1000}


def register(registry):
    async def _call(*args, **kwargs):
        return await find_files(registry, *args, **kwargs)
    registry.register('find_files', _call, '按 glob 查找文件', {"pattern": '如 **/*.log'})
