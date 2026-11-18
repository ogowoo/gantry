"""python_symbols tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import python_symbol_table, mark_read


async def python_symbols(registry, path: str) -> dict[str, Any]:
    """按 AST 列出 Python 符号与行号，便于按函数/类精确修改。"""
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    if target.suffix.lower() != ".py":
        raise ToolError("python_symbols 只支持 .py 文件")
    try:
        text = target.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本: {exc}") from exc
    mark_read(registry, path)
    try:
        symbols = python_symbol_table(text)
    except SyntaxError as exc:
        raise ToolError(f"Python 语法错误，无法解析符号: {exc}") from exc
    return {"path": path, "symbols": symbols, "count": len(symbols)}


def register(registry):
    async def _call(*args, **kwargs):
        return await python_symbols(registry, *args, **kwargs)
    registry.register('python_symbols', _call, '按 AST 列出 .py 文件里的函数/类及其行区间（含 Class.method 限定名）；改整个函数/类前先用它定位', {"path": '相对路径'})
