"""append_text tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from ..tools import ToolError
from ._files_utils import check_plain_code_size, hash_file


async def append_text(registry, path: str, content: str, via_b64: bool = False) -> dict[str, Any]:
    if len(content) > registry.max_write:
        raise ToolError("追加内容超过限制")
    if not via_b64:
        check_plain_code_size(registry, path, content)
    target = registry._path(path)
    registry._snap("append_text", path, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    before=hash_file(target)
    with target.open("a", encoding="utf-8", newline="") as f: f.write(content)
    after=hash_file(target)
    return {"path":path,"appended_chars":len(content),"file_event":{"tool":"append_text","path":path,"before_hash":before,"after_hash":after,"result":"ok"}}


def register(registry):
    async def _call(*args, **kwargs):
        return await append_text(registry, *args, **kwargs)
    registry.register('append_text', _call, '追加 UTF-8 明文；文档类分块续写用它，代码类仅限 ≤6000 字符（更长用 append_text_b64）', {"path": '相对路径', "content": '文本'})
