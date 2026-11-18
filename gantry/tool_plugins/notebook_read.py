"""notebook_read tool plugin: read Jupyter notebook cells (Claude Code NotebookRead)."""
from __future__ import annotations

import json
from typing import Any

from ..tools import ToolError
from ._files_utils import mark_read


def _cell_source(cell: dict[str, Any]) -> str:
    src = cell.get("source") or []
    if isinstance(src, list):
        return "".join(str(x) for x in src)
    return str(src)


async def notebook_read(registry, path: str, max_chars: int = 2000) -> dict[str, Any]:
    """读取 .ipynb 的单元格（类型/源码/执行序号/输出摘要）。"""
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    if target.suffix.lower() != ".ipynb":
        raise ToolError("notebook_read 只支持 .ipynb 文件")
    mark_read(registry, path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ToolError(f"不是有效的 notebook JSON: {exc}")
    try:
        cap = max(200, min(int(max_chars or 2000), 20000))
    except (TypeError, ValueError):
        raise ToolError("max_chars 必须是整数")
    cells = data.get("cells") or []
    out = []
    for idx, cell in enumerate(cells):
        src = _cell_source(cell)
        item: dict[str, Any] = {
            "index": idx,
            "cell_type": cell.get("cell_type"),
            "source": src[:cap],
            "truncated": len(src) > cap,
        }
        if cell.get("cell_type") == "code":
            item["execution_count"] = cell.get("execution_count")
            outputs = cell.get("outputs") or []
            item["output_count"] = len(outputs)
        out.append(item)
    return {"path": path, "nbformat": data.get("nbformat"),
            "nbformat_minor": data.get("nbformat_minor"),
            "cell_count": len(out), "cells": out}


def register(registry):
    async def _call(*args, **kwargs):
        return await notebook_read(registry, *args, **kwargs)
    registry.register(
        "notebook_read",
        _call,
        "读取 Jupyter notebook（.ipynb）的单元格：类型、源码、执行序号与输出数量；改 notebook 前先用它定位",
        {"path": "相对路径（.ipynb）", "max_chars": "每个单元格最多返回字符数，默认 2000"},
    )
