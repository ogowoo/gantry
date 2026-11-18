"""subagent_interrupt tool plugin: ask a running subagent to stop."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError

_TERMINAL = {"completed", "failed", "cancelled", "expired", "archived"}


async def subagent_interrupt(registry, agent_id: str, reason: str = "") -> dict[str, Any]:
    """请求终止一个子代理（协作式：置 cancel_requested，下一检查点退出）。"""
    from .. import bridge as b
    sid = str(agent_id or "").strip()
    if not sid:
        raise ToolError("agent_id 不能为空")
    info = b.AGENT_SESSIONS.get(sid)
    if not info:
        raise ToolError(f"没有会话 {sid}")
    if not str(info.get("parent_id") or "").strip():
        raise ToolError(f"{sid} 不是子代理会话，不能从子代理控制面中断")
    state = str(info.get("state") or "")
    if state in _TERMINAL:
        return {"agent_id": sid, "state": state, "cancelled": False,
                "note": f"会话已是终态（{state}），无需中断"}
    note = str(reason or "").strip() or "父任务请求中断子代理"
    b.AGENT_SESSIONS.patch(sid, state="cancelled", cancel_requested=True, last_error=note)
    return {"agent_id": sid, "state": "cancelled", "cancelled": True, "reason": note,
            "note": "已请求中断；子代理会在下一个检查点退出，可能仍有一小段在跑。"}


def register(registry):
    async def _call(*args, **kwargs):
        return await subagent_interrupt(registry, *args, **kwargs)
    registry.register(
        "subagent_interrupt",
        _call,
        "终止一个运行中的子代理（协作式中断；终态会话不会重复处理）",
        {"agent_id": "子代理会话 id", "reason": "中断原因（可选）"},
    )
