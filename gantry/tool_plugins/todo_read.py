"""todo_read tool plugin: read the current task list (Claude Code TodoRead)."""
from __future__ import annotations

from typing import Any


async def todo_read(registry) -> dict[str, Any]:
    """读取当前任务的 todo 清单（由 todo_write 写入，存在会话状态里）。"""
    getter = getattr(registry, "state_getter", None)
    state: dict[str, Any] = {}
    if callable(getter):
        try:
            state = getter() or {}
        except Exception:
            state = {}
    todos = state.get("todos") or []
    if not isinstance(todos, list):
        todos = []
    return {"count": len(todos), "todos": todos}


def register(registry):
    async def _call(*args, **kwargs):
        return await todo_read(registry, *args, **kwargs)
    registry.register(
        "todo_read",
        _call,
        "读取当前任务清单（todo_write 写入的进度），继续长任务前先确认还剩哪些项",
        {},
    )
