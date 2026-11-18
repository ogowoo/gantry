"""replace_python_symbol tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import python_symbol_table, require_read
from .replace_lines import replace_lines


async def replace_python_symbol(registry, path: str, name: str,
                                content: str) -> dict[str, Any]:
    """用 AST 定位函数/类，整段替换（比按文本匹配更稳）。"""
    if not str(name or "").strip():
        raise ToolError("name 不能为空")
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    require_read(registry, path)
    try:
        text = target.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本: {exc}") from exc
    try:
        symbols = python_symbol_table(text)
    except SyntaxError as exc:
        raise ToolError(f"Python 语法错误，无法定位符号: {exc}") from exc
    hit = next((s for s in symbols if s["name"] == str(name).strip()), None)
    if not hit:
        names = ", ".join(s["name"] for s in symbols[:20]) or "（无）"
        raise ToolError(f"未找到 Python 符号: {name}。可用符号: {names}")
    start, end = int(hit["start"]), int(hit["end"])
    if start < 1 or end < start:
        raise ToolError(f"符号 {name} 的行号范围无效: {start}-{end}")
    return await replace_lines(registry, path, start, end - start + 1, content)


def register(registry):
    async def _call(*args, **kwargs):
        return await replace_python_symbol(registry, *args, **kwargs)
    registry.register('replace_python_symbol', _call, '按 AST 定位并整段替换一个 Python 函数/类；比文本匹配更稳，改整段实现优先用它', {"path": '相对路径', "name": '符号名，如 Class.method 或 func', "content": '新的完整符号源码（含 def/class 行与缩进）'})
