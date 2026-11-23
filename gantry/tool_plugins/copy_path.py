"""copy_path tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from ..tools import ToolError


async def copy_path(registry, source: str, destination: str,
                    overwrite: bool = False) -> dict[str, Any]:
    src, dst = registry._path(source), registry._path(destination)
    if not src.exists():
        raise ToolError(f"源路径不存在: {source}")
    if dst == src or src in dst.parents:
        raise ToolError("禁止把目录复制/移动到它自己的子目录里")
    overwritten: list[str] = []
    if src.is_dir():
        if dst.exists():
            if not overwrite:
                raise ToolError(
                    f"目标目录已存在: {destination}。把 {source} 合并进已有目录"
                    "必须显式传 overwrite=true（已有同名文件会被覆盖）。")
            # copytree(dirs_exist_ok=True) 会覆盖 dst 下同名文件：
            # 逐一快照，回滚时才能还原被覆盖的内容
            for item in sorted(src.rglob("*")):
                if not item.is_file():
                    continue
                rel = item.relative_to(src)
                target = dst / rel
                if target.is_file():
                    rel_str = str(Path(destination) / rel).replace("\\", "/")
                    registry._snap("copy_path", rel_str, target)
                    overwritten.append(rel_str)
        registry._snap("copy_path", destination, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        if dst.is_file():
            registry._snap("copy_path", destination, dst)
            overwritten.append(str(destination).replace("\\", "/"))
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return {"source": source, "destination": destination, "copied": True,
            "overwritten": overwritten}


def register(registry):
    async def _call(*args, **kwargs):
        return await copy_path(registry, *args, **kwargs)
    registry.register('copy_path', _call, '复制文件或目录；合并进已存在目录时需 overwrite=true，被覆盖的文件会逐一快照可回滚', {"source": '相对路径', "destination": '相对路径', "overwrite": '目标目录已存在时允许合并覆盖（默认 false 会拒绝）'})
