"""session_changes tool plugin: list files changed in a session."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError


async def session_changes(registry, session_id: str = "") -> dict[str, Any]:
    """列出某个会话里被文件工具写过的文件（按路径聚合，保留最后一次结果）。"""
    from .. import bridge as b
    sid = str(session_id or "").strip() or str(registry.session_id() or "")
    if not sid:
        raise ToolError("没有当前会话 id，请显式传 session_id")
    events = b.AGENT_SESSIONS.file_events(sid)
    by_path: dict[str, dict[str, Any]] = {}
    for ev in events:
        path = str(ev.get("path") or "")
        if not path:
            continue
        item = by_path.get(path)
        if item is None:
            item = by_path[path] = {"path": path, "tools": [], "count": 0,
                                    "result": ev.get("result"), "at": ev.get("created")}
        item["count"] += 1
        tool = str(ev.get("tool") or "")
        if tool and tool not in item["tools"]:
            item["tools"].append(tool)
        item["result"] = ev.get("result") or item["result"]
        item["at"] = ev.get("created") or item["at"]
    changed = sorted(by_path.values(), key=lambda x: str(x.get("path")))
    return {"session_id": sid, "total": len(changed), "changed": changed}


def register(registry):
    async def _call(*args, **kwargs):
        return await session_changes(registry, *args, **kwargs)
    registry.register(
        "session_changes",
        _call,
        "列出本会话（或指定会话）里被文件工具改过的文件与最后一次结果，收尾 review 时用它",
        {"session_id": "会话 id（默认当前会话）"},
    )
