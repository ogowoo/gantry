# -*- coding: utf-8 -*-
"""任务图编排器（Batch 2）。

职责边界：
- 从 TaskStore 取 ready 任务；
- 只把 `executor=subagent` 的任务交给注入的 run_subagent；
- 按依赖 wave、并发上限和声明产物冲突调度；
- 审查实际产物，失败按 max_retries 返工；
- 把状态/证据写回 TaskStore。

不创建第二套子 Agent 运行时：run_subagent 由 bridge/tools 注入，实际仍是
gantry.subagents.run_subagent。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .task_plan import TaskStore, TaskPlanError
from .task_verify import independent_review, review_task

RunSubagent = Callable[[str, str, str, int], Awaitable[dict[str, Any]]]
ReviewFn = Callable[[dict[str, Any], dict[str, Any], Path], dict[str, Any]]


def _task_prompt(task: dict[str, Any]) -> str:
    lines = [
        f"你是子代理，只完成一个任务（ID: {task['id']}）。",
        f"标题：{task['title']}",
        f"描述：{task.get('description') or '（无）'}",
    ]
    if task.get("input") is not None:
        lines.append("输入：" + json.dumps(task["input"], ensure_ascii=False)[:4000])
    outputs = task.get("outputs") or []
    if outputs:
        lines.append("必须交付的产物（相对当前工作区，真实写到磁盘）：")
        for item in outputs:
            if isinstance(item, str):
                lines.append(f"- {item}")
            elif isinstance(item, dict):
                lines.append("- " + str(item.get("path") or item))
    acceptance = task.get("acceptance") or []
    if acceptance:
        lines.append("验收条件（必须真实满足，不要只声称完成）：")
        lines.append(json.dumps(acceptance, ensure_ascii=False)[:4000])
    if task.get("failure_reason"):
        lines.append("上一轮失败原因，必须避免重复：" + str(task["failure_reason"])[:1500])
    lines.append("要求：只做这一项；不要创建任务计划；不要修改与验收无关的文件；"
                 "完成后用 3 行以内汇报改了什么、验证结果、风险。")
    return "\n".join(lines)


def _paths(task: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for item in task.get("outputs") or []:
        raw = item if isinstance(item, str) else (
            item.get("path") if isinstance(item, dict) else "")
        if raw:
            try:
                out.add(str(Path(str(raw)).as_posix()).lstrip("./"))
            except Exception:
                out.add(str(raw))
    return out


def _pick_batch(ready: list[dict[str, Any]], cap: int,
                claimed: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从 ready 中挑一批无产物冲突的任务；返回 (batch, remaining)。"""
    batch: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    used: set[str] = set(claimed)
    for task in ready:
        paths = _paths(task)
        if paths & used:
            remaining.append(task)
            continue
        batch.append(task)
        used |= paths
        if len(batch) >= cap:
            remaining.extend(ready[ready.index(task) + 1:])
            break
    return batch, remaining


def _result_record(task: dict[str, Any], status: str, started: float,
                   *, result: dict[str, Any] | None = None,
                   review: dict[str, Any] | None = None,
                   error: str = "") -> dict[str, Any]:
    return {
        "task_id": task["id"], "title": task.get("title") or "",
        "status": status,
        "verified": bool((review or {}).get("verified")),
        "attempts": int(task.get("retry_count") or 0),
        "seconds": round(time.time() - started, 2),
        "agent_id": (result or {}).get("agent_id"),
        "content": str((result or {}).get("content") or "")[:4000],
        "review": review or {},
        "error": error[:2000],
    }


async def run_plan(store: TaskStore, session_id: str, run_subagent: RunSubagent,
                   *, model: str = "", max_concurrency: int = 4,
                   timeout_seconds: float = 600, max_steps: int = 0,
                   workspace: str | Path = ".", review_fn: ReviewFn | None = None,
                   allow_commands: bool = False, independent: bool = False,
                   ) -> dict[str, Any]:
    """执行任务图中的 subagent ready 任务，直到没有可执行项或计划被取消。"""
    sid = str(session_id or "").strip()
    if not sid:
        raise TaskPlanError("session_id 不能为空")
    cap = max(1, min(int(max_concurrency or 4), 8))
    timeout = max(0.05, float(timeout_seconds or 600))
    workspace = Path(workspace).resolve()
    results: list[dict[str, Any]] = []
    path_owner: dict[str, str] = {}
    initial = store.get_plan(sid) or {}
    for t in initial.get("tasks") or []:
        if t.get("status") == "completed":
            for p in _paths(t):
                path_owner[p] = str(t["id"])
    needs_main: list[str] = []
    started_all = time.time()

    while True:
        plan = store.get_plan(sid)
        if not plan or plan.get("status") == "cancelled":
            break
        by_id = {str(t["id"]): t for t in plan["tasks"]}
        ready = [by_id[tid] for tid in plan.get("ready") or [] if tid in by_id]
        if not ready:
            break
        sub_ready = [t for t in ready if t.get("executor") == "subagent"]
        for task in ready:
            if task.get("executor") != "subagent":
                needs_main.append(str(task["id"]))
        if not sub_ready:
            break

        # 已完成的产物路径不能被另一个任务覆盖；先判冲突再选批次。
        blocked_conflicts: list[dict[str, Any]] = []
        clean: list[dict[str, Any]] = []
        for task in sub_ready:
            owner = next((path_owner[p] for p in _paths(task) if p in path_owner), "")
            if owner:
                blocked_conflicts.append(task)
            else:
                clean.append(task)
        for task in blocked_conflicts:
            try:
                store.update_task(sid, task["id"], status="failed",
                                  failure_reason=f"产物路径已被任务 {owner} 占用，拒绝并行覆盖")
            except TaskPlanError:
                pass
            results.append(_result_record(task, "failed", time.time(),
                                          error="output path conflict"))
        if not clean:
            continue

        batch, _remaining = _pick_batch(clean, cap, set())
        if not batch:
            break
        for task in batch:
            try:
                store.update_task(sid, task["id"], status="running")
            except TaskPlanError as exc:
                results.append(_result_record(task, "failed", time.time(), error=str(exc)))
        batch = [t for t in batch if (store.get_plan(sid) or {}).get("tasks") and
                 any(x["id"] == t["id"] and x["status"] == "running"
                     for x in (store.get_plan(sid) or {}).get("tasks") or [])]
        if not batch:
            continue

        async def _one(task: dict[str, Any]) -> dict[str, Any]:
            started = time.time()
            prompt = _task_prompt(task)
            try:
                result = await asyncio.wait_for(
                    run_subagent(prompt, f"task:{task['id']}",
                                 str(task.get("model") or model), int(max_steps or 0)),
                    timeout=timeout)
            except asyncio.TimeoutError:
                result = {"error": f"子代理超时（>{timeout:.0f}s）", "content": ""}
            except Exception as exc:                     # noqa: BLE001
                result = {"error": f"{type(exc).__name__}: {exc}", "content": ""}
            if not isinstance(result, dict):
                result = {"error": "run_subagent 返回了非对象", "content": ""}

            if independent:
                verdict = await independent_review(
                    task, result, workspace, run_subagent,
                    model=str(task.get("model") or model),
                    allow_commands=allow_commands)
            elif review_fn is not None:
                verdict = review_fn(task, result, workspace)
            else:
                verdict = review_task(task, result, workspace,
                                      allow_commands=allow_commands)
            if verdict.get("ok"):
                for p in _paths(task):
                    path_owner[p] = str(task["id"])
                try:
                    store.update_task(sid, task["id"], status="completed",
                                      outputs=task.get("outputs") or [],
                                      failure_reason="")
                    store.note(sid, task["id"], "review",
                               json.dumps({"verified": verdict.get("verified"),
                                           "checks": verdict.get("checks")}, ensure_ascii=False))
                except TaskPlanError as exc:
                    return _result_record(task, "failed", started, result=result,
                                          review=verdict, error=str(exc))
                return _result_record(task, "completed", started,
                                      result=result, review=verdict)

            reason = str(verdict.get("reason") or "产物审查未通过")
            try:
                store.update_task(sid, task["id"], status="failed",
                                  failure_reason=reason)
                current = (store.get_plan(sid) or {}).get("tasks") or []
                mine = next((x for x in current if x["id"] == task["id"]), None)
                retries = int((mine or {}).get("retry_count") or 0)
                if retries <= int(task.get("max_retries") or 0):
                    store.resume(sid, task_id=task["id"])
                    store.note(sid, task["id"], "rework", reason)
                    return _result_record(task, "retrying", started,
                                          result=result, review=verdict, error=reason)
            except TaskPlanError as exc:
                reason = f"{reason}; {exc}"
            return _result_record(task, "failed", started, result=result,
                                  review=verdict, error=reason)

        recs = await asyncio.gather(*(_one(t) for t in batch))
        results.extend(recs)
        # 若一批全部失败/返工，下一轮会重新读取 store；避免永久循环：
        if all(r["status"] == "failed" for r in recs):
            # 独立 ready 任务可能还有可执行项；但若剩下全是 blocked，下一轮 ready 为空。
            continue

    plan = store.get_plan(sid) or {}
    return {
        "session_id": sid, "plan_id": plan.get("plan_id"),
        "status": plan.get("status"),
        "seconds": round(time.time() - started_all, 2),
        "requested": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "retrying": sum(1 for r in results if r["status"] == "retrying"),
        "verified": sum(1 for r in results if r["verified"]),
        "needs_main": sorted(set(needs_main)),
        "progress": plan.get("progress") or {},
        "results": results,
    }
