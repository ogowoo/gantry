"""write_text_b64 tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError
from ._files_utils import decode_b64
from .write_text import write_text


async def write_text_b64(registry, path: str, content_b64: str,
                         overwrite: bool = False) -> dict[str, Any]:
    content = decode_b64(content_b64)
    result = await write_text(registry, path, content, via_b64=True, overwrite=overwrite)
    result["tool"] = "write_text_b64"
    return result


def register(registry):
    async def _call(*args, **kwargs):
        return await write_text_b64(registry, *args, **kwargs)
    registry.register('write_text_b64', _call, 'Base64 解码后写入 UTF-8；**只用于新建文件**。修改现有文件必须先 read_text，再用 apply_patch/replace_text/replace_lines；确实要整体重写才传 overwrite=true', {"path": '相对路径', "content_b64": 'UTF-8 内容的 Base64', "overwrite": '仅整体重写现有文件时传 true（默认 false 会拒绝覆盖）'})
