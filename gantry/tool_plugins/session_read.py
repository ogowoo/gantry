"""session_read tool plugin: read a prior/current session's conversation and steps."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError


def _clip(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    return text[:limit] + ("…（已截断）" if len(text) > limit else "")


async def session_read(registry, session_id: str = "", limit: int = 50,
                       include_steps: bool = True) -> dict[str, Any]:
    """读取某个会话的消息与（可选）步骤；不传 session_id 时读当前会话。"""
    from .. import bridge as b
    sid = str(session_id or "").strip() or str(registry.session_id() or "")
    if not sid:
        raise ToolError("没有当前会话 id，请显式传 session_id")
    info = b.AGENT_SESSIONS.get(sid)
    if not info:
        raise ToolError(f"没有会话 {sid}")
    try:
        limit = max(1, min(int(limit or 50), 500))
    except (TypeError, ValueError):
        raise ToolError("limit 必须是整数")
    messages = []
    for item in b.AGENT_SESSIONS.messages(sid, limit=limit):
        messages.append({
            "role": item.get("role"),
            "content": _clip(item.get("content")),
            "created": item.get("created"),
            "conversation_index": item.get("conversation_index"),
        })
    out: dict[str, Any] = {
        "session_id": sid,
        "title": info.get("title"),
        "state": info.get("state"),
        "workspace": info.get("workspace"),
        "model": info.get("model"),
        "messages": messages,
        "message_count": len(messages),
    }
    if include_steps:
        steps = []
        for item in b.AGENT_SESSIONS.steps(sid, limit=limit):
            steps.append({
                "step": item.get("step"),
                "tool": item.get("tool"),
                "status": item.get("status"),
                "reason": _clip(item.get("reason"), 600),
                "error": _clip(item.get("error"), 1200),
                "result": _clip(item.get("result"), 1200),
            })
        out["steps"] = steps
        out["step_count"] = len(steps)
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await session_read(registry, *args, **kwargs)
    registry.register(
        "session_read",
        _call,
        "读取指定会话（默认当前会话）的消息与步骤，用于复盘上一次任务的过程与结论",
        {"session_id": "会话 id（默认当前会话）", "limit": "最多返回多少条消息/步骤，默认 50",
         "include_steps": "是否返回步骤，默认 true"},
    )
