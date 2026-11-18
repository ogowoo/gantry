"""append_text_b64 tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError
from ._files_utils import decode_b64
from .append_text import append_text


async def append_text_b64(registry, path: str, content_b64: str) -> dict[str, Any]:
    content = decode_b64(content_b64)
    result = await append_text(registry, path, content, via_b64=True)
    result["tool"] = "append_text_b64"
    return result


def register(registry):
    async def _call(*args, **kwargs):
        return await append_text_b64(registry, *args, **kwargs)
    registry.register('append_text_b64', _call, 'Base64 解码后追加 UTF-8；代码、JSON、HTML、Markdown 必须用此工具', {"path": '相对路径', "content_b64": 'UTF-8 内容的 Base64'})
