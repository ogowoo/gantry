"""list_directory tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import time as _t
from typing import Any
from ..tools import ToolError


async def list_directory(registry, path: str = ".", since_mtime: float | None = None) -> dict[str, Any]:
    target = registry._path(path)
    if not target.is_dir():
        raise ToolError(f"目录不存在: {path}")
    # "自上次列出以来的变化"：模型反复列目录多半是在确认有没有变。
    # 记住每个目录上次列出的时间（进程内），下一次列同一目录时顺带告诉它哪些文件是新/改的。
    key = str(target.resolve())
    cache = getattr(registry, "_dir_seen", None)
    if cache is None:
        cache = registry._dir_seen = {}
    prev = cache.get(key)
    items, changed = [], []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = item.stat()  # 悬空符号链接会抛错，跳过而不是拖垮整列
            entry = {"name": item.name, "type": "directory" if item.is_dir() else "file",
                     "size": stat.st_size, "mtime": int(stat.st_mtime)}
            items.append(entry)
            base = since_mtime if since_mtime is not None else prev
            if base is not None and stat.st_mtime > float(base):
                changed.append(item.name)
        except OSError:
            items.append({"name": item.name, "type": "unknown", "size": 0})
    import time as _t
    cache[key] = _t.time()
    out = {"path": path, "items": items[:1000], "truncated": len(items) > 1000}
    if changed:
        out["changed_since_last_list"] = sorted(changed)[:200]
        out["note"] = ("自上次列出以来有 %d 个条目发生变化（见 changed_since_last_list）——"
                       "只需要看这些，不必再逐层重列。" % len(changed))
    elif prev is not None and since_mtime is None:
        out["note"] = "自上次列出以来没有变化（内容与上次一致，可直接用之前的结果）。"
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await list_directory(registry, *args, **kwargs)
    registry.register('list_directory', _call, '列出工作区目录', {"path": '相对路径，默认 .', "since_mtime": '只返回在此时间戳之后修改的条目（可选）'})
