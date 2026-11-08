"""session_search tool plugin: search prior session history."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError


async def session_search(registry, query: str, limit: int = 10) -> dict[str, Any]:
    """跨会话全文搜索历史（messages + Agent steps），返回带上下文的片段。"""
    from .. import bridge as b
    q = str(query or "").strip()
    if not q:
        raise ToolError("query 不能为空")
    try:
        limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        raise ToolError("limit 必须是整数")
    messages = b.AGENT_SESSIONS.search_messages(q, limit=limit)
    steps = b.AGENT_SESSIONS.search_steps(q, limit=limit)
    return {
        "query": q,
        "count": len(messages) + len(steps),
        "messages": messages,
        "steps": steps,
        "note": "消息正文用 session_read 查看；步骤命中可直接看 snippet 或 session_read(include_steps=true)。",
    }


def register(registry):
    async def _call(*args, **kwargs):
        return await session_search(registry, *args, **kwargs)
    registry.register(
        "session_search",
        _call,
        "跨会话搜索历史对话与 Agent 步骤（按关键词），找“上次/之前做过什么、当时怎么解决的”时先用它",
        {"query": "关键词（字面匹配）", "limit": "每类最多返回条数，默认 10（上限 50）"},
    )
