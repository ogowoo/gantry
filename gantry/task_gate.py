# -*- coding: utf-8 -*-
"""任务图最终验收门。

主 Agent 返回 final 前，若当前会话存在活动任务图：
- 必须没有 pending/ready/running/blocked/failed 任务；
- 每个已完成任务必须通过 outputs/acceptance 的确定性检查；
- 有失败任务时拒绝完成，并给出可执行的修复提示。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .task_plan import TaskStore
from .task_verify import review_task


def verify_task_plan(session_id: str, workspace: str | Path,
                     final_content: str = "", *, store: TaskStore | None = None,
                     allow_commands: bool = False) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": True, "skipped": True, "reason": "no session"}
    try:
        store = store or TaskStore(
            Path(__file__).resolve().parent.parent / "data" / "tasks.db")
        plan = store.get_plan(sid)
    except Exception as exc:
        return {"ok": False, "skipped": False,
                "reason": f"任务图读取失败: {type(exc).__name__}: {exc}"}
    if not plan or plan.get("status") == "cancelled":
        return {"ok": True, "skipped": True, "reason": "no active task plan"}
    tasks = list(plan.get("tasks") or [])
    if not tasks:
        return {"ok": True, "skipped": True, "reason": "empty task plan"}
    open_tasks = [t for t in tasks
                  if str(t.get("status")) not in ("completed", "cancelled")]
    if open_tasks:
        detail = ", ".join(f"{t.get('id')}:{t.get('status')}" for t in open_tasks[:8])
        return {"ok": False, "skipped": False, "plan_id": plan.get("plan_id"),
                "reason": f"仍有未完成任务: {detail}",
                "open": [t.get("id") for t in open_tasks],
                "checks": []}
    checks = []
    for task in tasks:
        if str(task.get("status")) != "completed":
            continue
        verdict = review_task(task, {"content": final_content or ""},
                              workspace, allow_commands=allow_commands)
        checks.append({"task_id": task.get("id"), "ok": verdict.get("ok"),
                       "verified": verdict.get("verified"),
                       "reason": verdict.get("reason") or "",
                       "failed": verdict.get("failed") or []})
    failed = [c for c in checks if not c["ok"]]
    if failed:
        return {"ok": False, "skipped": False, "plan_id": plan.get("plan_id"),
                "reason": "; ".join(f"{c['task_id']}: {c['reason']}" for c in failed)[:1500],
                "failed": [c["task_id"] for c in failed], "checks": checks}
    return {"ok": True, "skipped": False, "plan_id": plan.get("plan_id"),
            "reason": "任务图验收通过", "checks": checks,
            "verified": sum(1 for c in checks if c["verified"])}
