"""replace_lines tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import diff_payload, hash_file, require_read


async def replace_lines(registry, path: str, start: int = 0, count: int = 1,
                        content: str = "") -> dict[str, Any]:
    """按行号替换整行区间（1 起，含首尾），保留文件原有换行风格。

    为什么需要：改缩进/整段替换用 `replace_text` 很脆——模型给的片段里
    前导空格容易在 JSON 里失真，实测一个真实任务因此反复失败，最后去用
    PowerShell 逐行改（然后踩了编码坑）。按行号改不依赖片段匹配。
    """
    try:
        start = int(start)
        count = max(1, min(int(count), 5000))
    except (TypeError, ValueError):
        raise ToolError("start / count 必须是整数")
    if start < 1:
        raise ToolError("start 从 1 开始（第 1 行）")
    if len(content) > registry.max_write:
        raise ToolError("替换内容超过限制")
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    require_read(registry, path)
    try:
        with target.open("r", encoding="utf-8", errors="strict", newline="") as f:
            raw = f.read()
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本，拒绝改写（防止乱码写回）: {exc}")
    crlf = "\r\n" in raw
    lines = raw.replace("\r\n", "\n").split("\n")
    trailing_nl = raw.endswith(("\n"))
    body = lines[:-1] if trailing_nl else lines[:]
    total = len(body)
    if start > total + 1:
        raise ToolError(f"start={start} 超出文件行数（共 {total} 行）")
    lo = start - 1
    hi = min(total, lo + count)
    new_lines = content.split("\n") if content != "" else []
    out_lines = body[:lo] + new_lines + body[hi:]
    text = "\n".join(out_lines) + ("\n" if trailing_nl or not body else "")
    # 行尾风格跟随原文件：CRLF 文件不因为这次编辑被改成 LF（避免整文件 diff）
    if crlf:
        text = text.replace("\n", "\r\n")
    before = hash_file(target)
    registry._snap("replace_lines", path, target)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    after = hash_file(target)
    out = {"path": path, "replaced_lines": hi - lo, "start": start,
           "inserted_lines": len(new_lines), "total_lines": len(out_lines),
           "changed": before != after,
           "file_event": {"tool": "replace_lines", "path": path,
                          "before_hash": before, "after_hash": after, "result": "ok"}}
    out.update(diff_payload(path, raw, text))
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await replace_lines(registry, *args, **kwargs)
    registry.register('replace_lines', _call, '按行号替换整行区间（1 起，含首尾），保留文件原有换行风格；改缩进/整段替换时用它，比 replace_text 更稳；不需要 shell', {"path": '相对路径', "start": '起始行号，1 起', "count": '替换几行，默认1', "content": '新内容（可含多行；传空串表示删除这几行）'})
