"""read_text tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import mark_read


async def read_text(registry, path: str, offset: int = 0, limit: int = 0,
                    line_start: int = 0, line_count: int = 0,
                    reveal_whitespace: bool = False) -> dict[str, Any]:
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    mark_read(registry, path)
    offset = max(0, registry._int_arg(offset, 0, "offset"))
    limit = max(0, registry._int_arg(limit, 0, "limit"))
    line_start = max(0, registry._int_arg(line_start, 0, "line_start"))
    line_count = max(0, registry._int_arg(line_count, 0, "line_count"))
    # 按行读（line_start 1 起）：真实任务里模型要"看第 514 行/改缩进"，
    # 而 offset 是**字符**偏移，表达不了"第几行"，于是它去用
    # PowerShell 的 (Get-Content x)[514] —— 那一步就踩到编码坑（见
    # for debug 里那份时间线：Set-Content 把整文件写成乱码，花 10 步修）。
    # 这里给出行号与精确行区间，按行定位不再需要 shell。
    if line_start:
        try:
            with target.open("r", encoding="utf-8", newline="") as f:
                raw = f.read()
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是有效的 UTF-8 文本: {exc}")
        lines = raw.replace("\r\n", "\n").split("\n")
        total_lines = len(lines) - (1 if lines and lines[-1] == "" else 0)
        if line_start > total_lines:
            return {"path": path, "lines": "", "line_start": line_start,
                    "line_count": 0, "total_lines": total_lines, "truncated": False}
        want = line_count or (total_lines - line_start + 1)
        want = max(1, min(want, 2000, registry.max_read // 40 or 1))
        lo = line_start - 1
        hi = min(total_lines, lo + want)
        def _vis(t: str) -> str:
            # 空白可视化：空格→·、制表符→→。模型以前只能用
            # PowerShell 的 .Replace(' ','·') 才看得见缩进差异（还会踩编码坑），
            # 直接给它这个开关更省回合。
            return t.replace("\t", "→").replace(" ", "·")
        body = "\n".join(
            ((f"{i + 1}: " + _vis(lines[i])) if reveal_whitespace
             else f"{i + 1}: {lines[i]}") for i in range(lo, hi))
        _res = {"path": path, "lines": body, "line_start": line_start,
                "line_count": hi - lo, "total_lines": total_lines,
                "truncated": hi < total_lines, "numbered": True}
        if reveal_whitespace:
            _res["whitespace"] = "空格=·  制表符=→"
        return _res
    if offset or limit:
        # 分段读取：整文件统计 total_chars 后按字符切片。供 Agent 翻页
        # 读大文件——单次读取的上下文预算约 6000 字符，超过就读不到尾部，
        # 必须靠 offset 定位。limit 封顶 max_read，防止一次灌爆上下文。
        try:
            with target.open("r", encoding="utf-8", newline="") as f:
                full = f.read()
        except UnicodeDecodeError as exc:
            raise ToolError(f"文件不是有效的 UTF-8 文本: {exc}")
        total = len(full)
        if offset >= total:
            return {"path": path, "content": "", "offset": offset, "returned_chars": 0,
                    "total_chars": total, "truncated": False}
        cap = min(limit, registry.max_read) if limit else registry.max_read
        end = min(total, offset + cap)
        return {"path": path, "content": full[offset:end], "offset": offset,
                "returned_chars": end - offset, "total_chars": total,
                "truncated": end < total,
                "next_offset": end if end < total else None}
    # 流式读 max_read+1 个字符即停：GB 级文件不会全量进内存。
    # newline="" 不做换行转换，读到什么是什么。
    with target.open("r", encoding="utf-8", errors="replace", newline="") as f:
        text = f.read(registry.max_read + 1)
    truncated = len(text) > registry.max_read
    if truncated:
        text = text[:registry.max_read]
    result = {"path": path, "content": text, "truncated": truncated}
    if truncated:
        result["hint"] = "文件超过单次读取上限，已截断：请用 offset 参数分段读取全文"
    return result


def register(registry):
    async def _call(*args, **kwargs):
        return await read_text(registry, *args, **kwargs)
    registry.register('read_text', _call, '读取 UTF-8 文本；默认读全文件（上限 max_read_chars）。超大文件用 offset/limit 按字符分段，或 line_start/line_count 按行读（带行号，便于精确改某几行）', {"path": '相对路径', "offset": '字符偏移，默认0', "limit": '本次最多返回字符数，默认120000', "line_start": '按行读：起始行号，1 起（传了就忽略 offset）', "line_count": '按行读：读多少行，默认到文件末尾', "reveal_whitespace": '把空格显示成 ·，把制表符显示成 →（查缩进必用）'})
