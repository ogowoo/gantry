"""write_text tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import check_plain_code_size, diff_payload, hash_file, require_read


async def write_text(registry, path: str, content: str, via_b64: bool = False,
                     overwrite: bool = False) -> dict[str, Any]:
    if len(content) > registry.max_write:
        raise ToolError("写入内容超过限制")
    # via_b64=True 时不限明文长度：Base64 分块就是代码类大文件的正路
    if not via_b64:
        check_plain_code_size(registry, path, content)
    target = registry._path(path)
    existed = target.is_file() and target.stat().st_size > 0
    if existed and not overwrite:
        raise ToolError(
            f"文件已存在: {path}。修改现有文件请用 apply_patch / replace_text / replace_lines "
            "做局部修改；确实要整体重写时才传 overwrite=true（不要为了省事整篇覆盖）。")
    if existed:
        require_read(registry, path)
    before_text = ""
    if existed:
        try:
            with target.open("r", encoding="utf-8", errors="replace", newline="") as f:
                before_text = f.read()
        except Exception:
            before_text = ""
    registry._snap("write_text", path, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    before=hash_file(target)
    # newline=""：原样写入，Windows 下不把 \n 静默转成 \r\n
    with target.open("w", encoding="utf-8", newline="") as f: f.write(content)
    after=hash_file(target)
    out = {"path":path,"written_chars":len(content),
           "file_event":{"tool":"write_text","path":path,"before_hash":before,"after_hash":after,"result":"ok"}}
    out.update(diff_payload(path, before_text, content))
    if existed and out.get("changed_lines", 0):
        out["note"] = "本次为整体覆盖写入；已返回 diff。后续修改现有文件优先用 apply_patch / replace_text。"
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await write_text(registry, *args, **kwargs)
    registry.register('write_text', _call, '写入 UTF-8 明文；**只用于新建文件**。已存在的文件必须先 read_text，再用 apply_patch/replace_text/replace_lines 局部修改；确实要整体重写才传 overwrite=true。文档类（.md/.txt/.log）可整篇写，代码类仅限 ≤6000 字符（更长用 b64）', {"path": '相对路径', "content": '文本', "overwrite": '仅整体重写现有文件时传 true（默认 false 会拒绝覆盖）'})
