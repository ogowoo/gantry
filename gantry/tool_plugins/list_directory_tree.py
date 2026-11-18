"""list_directory_tree tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from ..tools import ToolError


async def list_directory_tree(registry, path: str = ".", max_depth: int = 3,
                              include_hidden: bool = False,
                              max_entries: int = 1000) -> dict[str, Any]:
    """递归列出目录树（含子目录与文件），解决模型幻觉 `self_list_tree` 的问题。

    返回扁平列表，每条带 depth，方便模型快速把握项目结构；命中上限时截断并提示。
    """
    target = registry._path(path)
    if not target.is_dir():
        raise ToolError(f"目录不存在: {path}")
    max_depth = max(0, min(registry._int_arg(max_depth, 3, "max_depth"), 8))
    max_entries = max(1, min(registry._int_arg(max_entries, 1000, "max_entries"), 2000))
    include_hidden = bool(include_hidden)

    items: list[dict[str, Any]] = []
    truncated = False

    for root, dirs, files in os.walk(target, topdown=True):
        current = Path(root)
        try:
            rel = current.relative_to(target)
        except ValueError:
            continue
        depth = len(rel.parts) if str(rel) != "." else 0
        if depth > max_depth:
            del dirs[:]
            continue

        if not include_hidden:
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files = [f for f in files if not f.startswith(".")]

        # 目录条目（根目录也保留，作为锚点）
        if depth <= max_depth:
            try:
                stat = current.stat()
                items.append({
                    "path": rel.as_posix() if str(rel) != "." else ".",
                    "type": "directory",
                    "size": 0,
                    "mtime": int(stat.st_mtime),
                    "depth": depth,
                })
            except OSError:
                pass

        for name in sorted(files):
            if len(items) >= max_entries:
                truncated = True
                break
            fpath = current / name
            frel = rel / name if str(rel) != "." else Path(name)
            try:
                stat = fpath.stat()
                items.append({
                    "path": frel.as_posix(),
                    "type": "file",
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime),
                    "depth": depth + 1,
                })
            except OSError:
                pass

        if truncated:
            del dirs[:]
            break

    out: dict[str, Any] = {
        "path": path,
        "items": items,
        "truncated": truncated,
        "max_depth": max_depth,
        "include_hidden": include_hidden,
    }
    if truncated:
        out["note"] = f"条目数超过 {max_entries}，已截断；需要更深层/更精确的内容请用 find_files 或 read_text。"
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await list_directory_tree(registry, *args, **kwargs)
    registry.register('list_directory_tree', _call, '递归列出目录树（含子目录与文件），用于全量 Review 项目结构；需要精确查找用 find_files，不存在 self_list_tree', {"path": '相对路径，默认 .', "max_depth": '最大递归深度，默认 3（范围 0-8）', "include_hidden": '是否包含隐藏文件/目录，默认 false', "max_entries": '最多返回条目数，默认 1000'})
