"""indent_lines tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import re
from typing import Any
from ..tools import ToolError
from ._files_utils import hash_file, require_read


async def indent_lines(registry, path: str, line_start: int = 1, line_count: int = 1,
                       delta: int = 0) -> dict[str, Any]:
    """给指定行区间**增减行首空格**（delta 为正加缩进、为负减缩进）。

    为什么不能靠 replace_text：缩进要写在参数字符串里，而 Copilot 的回答经
    HTML 渲染后**行首/连续空格会被折叠成一个**,于是 old == new、写入无变化、
    模型以为没生效又改一次（真实时间线里 15 次空操作、空转几十步）。
    这里用**数字**表达缩进，不依赖字符串里的空白，一次调用确定生效。
    """
    try:
        line_start = int(line_start or 1)
        line_count = int(line_count or 1)
        delta = int(delta or 0)
    except (TypeError, ValueError):
        raise ToolError("line_start / line_count / delta 必须是整数")
    if line_start < 1:
        raise ToolError("line_start 从 1 开始")
    if line_count < 1:
        raise ToolError("line_count 至少 1")
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    try:
        with target.open("r", encoding="utf-8", errors="strict", newline="") as f:
            raw = f.read()
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本，拒绝改写（防止乱码写回）: {exc}")
    crlf = "\r\n" in raw
    lines = raw.replace("\r\n", "\n").split("\n")
    lo = line_start - 1
    hi = min(len(lines), lo + line_count)
    if lo >= len(lines):
        raise ToolError(f"line_start={line_start} 超出文件行数（共 {len(lines)} 行）")
    changed = 0
    for i in range(lo, hi):
        body = lines[i]
        if not body.strip():
            continue                      # 空行不动
        m = re.match(r"[ \t]*", body)
        indent = m.group(0) if m else ""
        rest = body[len(indent):]
        n = len(indent.expandtabs(4))
        n2 = max(0, n + delta)
        if n2 == n and "\t" not in indent:
            continue
        new_indent = " " * n2
        if new_indent + rest != body:
            lines[i] = new_indent + rest
            changed += 1
    if not changed:
        return {"path": path, "changed_lines": 0, "changed": False,
                "hint": "这些行的缩进已经是目标值，无需改动"}
    text = "\n".join(lines)
    if crlf:
        text = text.replace("\n", "\r\n")
    before = hash_file(target)
    registry._snap("indent_lines", path, target)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    after = hash_file(target)
    return {"path": path, "changed_lines": changed, "delta": delta,
            "changed": before != after,
            "file_event": {"tool": "indent_lines", "path": path,
                           "before_hash": before, "after_hash": after, "result": "ok"}}


def register(registry):
    async def _call(*args, **kwargs):
        return await indent_lines(registry, *args, **kwargs)
    registry.register('indent_lines', _call, '给指定行区间**增减行首空格**（delta 正=加缩进，负=减）；修缩进用它，别在 replace_text 里塞空格', {"path": '相对路径', "line_start": '起始行，1 起', "line_count": '多少行，默认1', "delta": '加减几个空格（正=加，负=减）'})
