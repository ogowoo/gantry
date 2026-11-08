"""convert_indentation tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import re
from typing import Any
from ..tools import ToolError
from ._files_utils import hash_file, require_read


async def convert_indentation(registry, path: str, mode: str = "tabs2spaces",
                              width: int = 4, line_start: int = 0,
                              line_count: int = 0) -> dict[str, Any]:
    """把**行首**缩进在制表符与空格之间确定性地转换（一次搞定）。

    为什么需要单列一个工具：真实任务里模型要"把制表符统一换成 4 个空格"，
    只能用 replace_text 一处一处改（默认 count=1），改一处、读一次、
    发现还有、再改……一个 377 步的任务里几十步都耗在这上面，还换了 6 次
    Copilot 会话（用户原话："一直在重复替换空格/制表的动作"）。
    这个工具只动**行首空白**（行内、字符串里的制表符一律不碰），
    一次调用完成全文件或指定行区间，并回报改了多少行。
    """
    mode = str(mode or "tabs2spaces").strip().lower()
    if mode in ("tab2space", "tabs2space", "expand", "spaces"):
        mode = "tabs2spaces"
    elif mode in ("space2tab", "spaces2tab", "compress", "tabs"):
        mode = "spaces2tabs"
    if mode not in ("tabs2spaces", "spaces2tabs"):
        raise ToolError("mode 只能是 tabs2spaces 或 spaces2tabs")
    try:
        width = max(1, min(int(width), 16))
        line_start = int(line_start or 0)
        line_count = int(line_count or 0)
    except (TypeError, ValueError):
        raise ToolError("width / line_start / line_count 必须是整数")
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
    lo = max(0, (line_start - 1) if line_start > 0 else 0)
    hi = len(lines) if not line_count else min(len(lines), lo + max(0, line_count))
    changed = 0
    out = list(lines)
    for i in range(lo, hi):
        body = lines[i]
        # 只取行首空白（空格与制表符），其余原样保留
        m = re.match(r"[ \t]*", body)
        indent = m.group(0) if m else ""
        rest = body[len(indent):]
        if mode == "tabs2spaces":
            if "\t" not in indent:
                continue
            # 制表位按 width 展开（4 空格制表位）
            new_indent = indent.expandtabs(width)
        else:
            if " " not in indent:
                continue
            n = len(indent)
            new_indent = "\t" * (n // width) + " " * (n % width)
        if new_indent != indent:
            out[i] = new_indent + rest
            changed += 1
    if not changed:
        return {"path": path, "changed_lines": 0, "changed": False,
                "hint": "行首缩进已经符合目标格式，无需改动"}
    text = "\n".join(out)
    if crlf:
        text = text.replace("\n", "\r\n")
    before = hash_file(target)
    registry._snap("convert_indentation", path, target)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write(text)
    after = hash_file(target)
    return {"path": path, "mode": mode, "width": width,
            "changed_lines": changed, "changed": before != after,
            "line_range": [lo + 1, hi],
            "file_event": {"tool": "convert_indentation", "path": path,
                           "before_hash": before, "after_hash": after, "result": "ok"}}


def register(registry):
    async def _call(*args, **kwargs):
        return await convert_indentation(registry, *args, **kwargs)
    registry.register('convert_indentation', _call, '把**行首**缩进在制表符/空格之间一次转换完（只动行首，不碰行内与字符串）；修缩进别再用 replace_text 一处一处改', {"path": '相对路径', "mode": 'tabs2spaces（默认）或 spaces2tabs', "width": '制表位宽度，默认4', "line_start": '只改这一段：起始行，1 起（可省略）', "line_count": '改多少行（可省略）'})
