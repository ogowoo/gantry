# -*- coding: utf-8 -*-
"""持久任务图：Task 模型、依赖图算法和 SQLite 存储。

这一层刻意不依赖 AgentRunner / bridge：
- 纯图函数可以被 Agent、工具、测试和未来的 Orchestrator 共用；
- 存储独立于 `sessions` 表，避免为了一个 P0 功能给核心会话库做高风险迁移；
- 任务通过 session_id 归属会话，`task_ready()` 只返回依赖已完成的任务。
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

TASK_STATUSES = ("pending", "ready", "running", "blocked", "completed", "failed", "cancelled")
PLAN_STATUSES = ("active", "completed", "cancelled")
EXECUTORS = ("main", "subagent")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS task_plans (
  plan_id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  revision INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_plans_session ON task_plans(session_id, updated);
CREATE TABLE IF NOT EXISTS task_items (
  plan_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 0,
  executor TEXT NOT NULL DEFAULT 'main',
  assignee TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  depends_on TEXT NOT NULL DEFAULT '[]',
  input TEXT NOT NULL DEFAULT 'null',
  outputs TEXT NOT NULL DEFAULT '[]',
  history TEXT NOT NULL DEFAULT '[]',
  acceptance TEXT NOT NULL DEFAULT '[]',
  failure_reason TEXT NOT NULL DEFAULT '',
  retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 1,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  PRIMARY KEY (plan_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_task_items_plan ON task_items(plan_id, ordinal);
"""


class TaskPlanError(ValueError):
    """任务图的用户可纠正错误。"""


def _now() -> float:
    return time.time()


def _jdump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _jload(text: str, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except Exception:
        return default


def _clean_id(raw: Any, fallback: str) -> str:
    text = str(raw or "").strip()
    if not text:
        text = fallback
    if not text:
        return ""
    if not _ID_RE.match(text):
        raise TaskPlanError(f"任务 ID 只能包含字母/数字/_.:-，收到: {text!r}")
    return text


def _norm_status(raw: Any, default: str = "pending") -> str:
    status = str(raw or default).strip().lower()
    if status not in TASK_STATUSES:
        raise TaskPlanError(f"任务状态必须是 {'/'.join(TASK_STATUSES)}，收到: {status!r}")
    return status


def _norm_executor(raw: Any) -> str:
    executor = str(raw or "main").strip().lower()
    if executor not in EXECUTORS:
        raise TaskPlanError(f"executor 必须是 {'/'.join(EXECUTORS)}，收到: {executor!r}")
    return executor


def _deps_of(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, (str, int)):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return list(raw)
    raise TaskPlanError("depends_on 必须是数组")


def normalize_tasks(raw_tasks: Iterable[Any], *, plan_id: str = "") -> list[dict[str, Any]]:
    """校验并补全任务数组。

    - 不提供 id 时依次生成 t1/t2...；
    - depends_on 支持任务 ID、1-based 序号和数字；
    - 只做结构/依赖校验，不自动执行任务。
    """
    if not isinstance(raw_tasks, (list, tuple)):
        raise TaskPlanError("tasks 必须是数组")
    raw_list = list(raw_tasks)
    if not raw_list:
        raise TaskPlanError("tasks 不能为空")
    if len(raw_list) > 50:
        raise TaskPlanError("一个计划最多 50 个任务")

    ids: list[str] = []
    used: set[str] = set()
    for i, raw in enumerate(raw_list, 1):
        if isinstance(raw, str):
            raw = {"title": raw}
        if not isinstance(raw, dict):
            raise TaskPlanError(f"第 {i} 个任务必须是对象")
        tid = _clean_id(raw.get("id") or raw.get("task_id"), f"t{i}")
        if tid in used:
            raise TaskPlanError(f"任务 ID 重复: {tid}")
        used.add(tid)
        ids.append(tid)

    tasks: list[dict[str, Any]] = []
    for i, (raw, tid) in enumerate(zip(raw_list, ids), 1):
        assert isinstance(raw, dict)
        title = str(raw.get("title") or raw.get("name") or f"任务 {i}").strip()
        if not title:
            raise TaskPlanError(f"任务 {tid} 缺少 title")
        deps_raw = _deps_of(raw.get("depends_on", raw.get("after", raw.get("deps"))))
        deps: list[str] = []
        for dep in deps_raw:
            if isinstance(dep, bool):
                raise TaskPlanError(f"任务 {tid} 的依赖无效: {dep!r}")
            if isinstance(dep, int):
                idx = dep - 1
                dep_id = ids[idx] if 0 <= idx < len(ids) else ""
            else:
                s = str(dep).strip()
                if s.isdigit():
                    idx = int(s) - 1
                    dep_id = ids[idx] if 0 <= idx < len(ids) else ""
                else:
                    dep_id = _clean_id(s, "")
            if not dep_id or dep_id not in ids:
                raise TaskPlanError(f"任务 {tid} 的依赖不存在: {dep!r}")
            if dep_id == tid:
                raise TaskPlanError(f"任务 {tid} 不能依赖自己")
            if dep_id not in deps:
                deps.append(dep_id)
        status = _norm_status(raw.get("status"), "pending")
        # 新建计划时未显式给状态的根任务直接 ready；有依赖的保持 pending。
        if not raw.get("status"):
            status = "ready" if not deps else "pending"
        task = {
            "id": tid,
            "title": title,
            "description": str(raw.get("description") or "")[:4000],
            "status": status,
            "priority": int(raw.get("priority") or 0),
            "executor": _norm_executor(raw.get("executor")),
            "assignee": str(raw.get("assignee") or "")[:200],
            "model": str(raw.get("model") or "")[:200],
            "depends_on": deps,
            "input": raw.get("input"),
            "outputs": list(raw.get("outputs") or []),
            "acceptance": list(raw.get("acceptance") or []),
            "failure_reason": str(raw.get("failure_reason") or "")[:2000],
            "retry_count": max(0, int(raw.get("retry_count") or 0)),
            "max_retries": max(0, int(raw.get("max_retries") or 0)),
            "history": list(raw.get("history") or []),
            "created": float(raw.get("created") or _now()),
            "updated": _now(),
        }
        tasks.append(task)

    validate_tasks(tasks)
    return tasks


def validate_tasks(tasks: list[dict[str, Any]]) -> None:
    """只做静态校验；不修改任务。"""
    ids = {str(t.get("id")) for t in tasks}
    if len(ids) != len(tasks):
        raise TaskPlanError("任务 ID 重复")
    for task in tasks:
        tid = str(task.get("id") or "")
        if tid not in ids:
            raise TaskPlanError(f"任务 ID 无效: {tid!r}")
        _norm_status(task.get("status"))
        _norm_executor(task.get("executor"))
        for dep in task.get("depends_on") or []:
            if str(dep) not in ids:
                raise TaskPlanError(f"任务 {tid} 的依赖不存在: {dep!r}")
            if str(dep) == tid:
                raise TaskPlanError(f"任务 {tid} 不能依赖自己")

    # Kahn 拓扑排序检测环；保留稳定顺序用于测试/展示。
    indeg = {str(t["id"]): 0 for t in tasks}
    children: dict[str, list[str]] = {tid: [] for tid in indeg}
    for task in tasks:
        tid = str(task["id"])
        for dep in task.get("depends_on") or []:
            indeg[tid] += 1
            children[str(dep)].append(tid)
    ready = [tid for tid, n in indeg.items() if n == 0]
    visited = 0
    while ready:
        tid = ready.pop(0)
        visited += 1
        for child in children[tid]:
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
    if visited != len(tasks):
        raise TaskPlanError("任务依赖存在循环")


def _by_id(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(t["id"]): t for t in tasks}


def ready_ids(tasks: list[dict[str, Any]]) -> list[str]:
    """返回可启动任务：状态 ready 且所有前置 completed，按优先级降序。"""
    by_id = _by_id(tasks)
    out = []
    for task in tasks:
        if task.get("status") != "ready":
            continue
        deps = [by_id[d] for d in task.get("depends_on") or [] if d in by_id]
        if all(str(d.get("status")) == "completed" for d in deps):
            out.append(task)
    out.sort(key=lambda t: (-int(t.get("priority") or 0), str(t.get("id"))))
    return [str(t["id"]) for t in out]


def downstream_ids(tasks: list[dict[str, Any]], changed: Iterable[str]) -> list[str]:
    """返回所有（直接/间接）依赖 changed 的任务 ID，不含 changed 自己。"""
    children: dict[str, list[str]] = {str(t["id"]): [] for t in tasks}
    for task in tasks:
        for dep in task.get("depends_on") or []:
            children.setdefault(str(dep), []).append(str(task["id"]))
    seen: set[str] = set()
    todo = [str(x) for x in changed]
    while todo:
        cur = todo.pop()
        for child in children.get(cur, []):
            if child not in seen and child != cur:
                seen.add(child)
                todo.append(child)
    return sorted(seen)


def recompute_statuses(tasks: list[dict[str, Any]], *, unblock: bool = True) -> None:
    """按依赖重算 pending/ready/blocked；不覆盖 completed/failed/cancelled/running。"""
    by_id = _by_id(tasks)
    for task in tasks:
        status = str(task.get("status"))
        if status in ("completed", "failed", "cancelled", "running"):
            continue
        deps = [by_id[d] for d in task.get("depends_on") or [] if d in by_id]
        dep_statuses = [str(d.get("status")) for d in deps]
        if any(s in ("failed", "cancelled", "blocked") for s in dep_statuses):
            task["status"] = "blocked"
            if not task.get("failure_reason"):
                task["failure_reason"] = "前置任务失败/取消"
        elif deps and not all(s == "completed" for s in dep_statuses):
            task["status"] = "pending"
            if task.get("failure_reason") == "前置任务失败/取消":
                task["failure_reason"] = ""
        elif all(s == "completed" for s in dep_statuses):
            if status in ("pending", "blocked") and unblock:
                task["status"] = "ready"
                task["failure_reason"] = ""
            elif status == "blocked" and not unblock:
                pass
        task["updated"] = _now()


def plan_progress(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {s: 0 for s in TASK_STATUSES}
    for task in tasks:
        status = str(task.get("status"))
        counts[status] = counts.get(status, 0) + 1
    return {"total": len(tasks), "completed": counts["completed"],
            "running": counts["running"], "ready": counts["ready"],
            "pending": counts["pending"], "blocked": counts["blocked"],
            "failed": counts["failed"], "cancelled": counts["cancelled"]}


def _plan_completed(tasks: list[dict[str, Any]]) -> bool:
    return bool(tasks) and all(str(t.get("status")) in ("completed", "cancelled") for t in tasks)


class TaskStore:
    """任务的 SQLite 存储；每次操作独立连接，使用 WAL。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(str(self.path), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _init(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA)
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(task_items)")}
            if "model" not in cols:
                con.execute("ALTER TABLE task_items ADD COLUMN model TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["task_id"]),
            "title": str(row["title"]),
            "description": str(row["description"]),
            "status": str(row["status"]),
            "priority": int(row["priority"]),
            "executor": str(row["executor"]),
            "assignee": str(row["assignee"]),
            "model": str(row["model"]) if "model" in row.keys() else "",
            "depends_on": _jload(row["depends_on"], []),
            "input": _jload(row["input"], None),
            "outputs": _jload(row["outputs"], []),
            "history": _jload(row["history"], []),
            "acceptance": _jload(row["acceptance"], []),
            "failure_reason": str(row["failure_reason"]),
            "retry_count": int(row["retry_count"]),
            "max_retries": int(row["max_retries"]),
            "created": float(row["created"]),
            "updated": float(row["updated"]),
        }

    def _load(self, con: sqlite3.Connection, plan_id: str) -> dict[str, Any] | None:
        plan = con.execute("SELECT * FROM task_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not plan:
            return None
        rows = con.execute("SELECT * FROM task_items WHERE plan_id=? ORDER BY ordinal",
                           (plan_id,)).fetchall()
        tasks = [self._task_from_row(r) for r in rows]
        return {
            "plan_id": str(plan["plan_id"]),
            "session_id": str(plan["session_id"]),
            "goal": str(plan["goal"]),
            "title": str(plan["title"]),
            "status": str(plan["status"]),
            "revision": int(plan["revision"]),
            "created": float(plan["created"]),
            "updated": float(plan["updated"]),
            "tasks": tasks,
            "progress": plan_progress(tasks),
            "ready": ready_ids(tasks),
        }

    @staticmethod
    def _write_plan(con: sqlite3.Connection, plan: dict[str, Any]) -> None:
        now = _now()
        con.execute(
            "INSERT INTO task_plans(plan_id,session_id,goal,title,status,revision,created,updated) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET "
            "session_id=excluded.session_id, goal=excluded.goal, title=excluded.title, "
            "status=excluded.status, revision=excluded.revision, updated=excluded.updated",
            (plan["plan_id"], plan["session_id"], plan.get("goal") or "",
             plan.get("title") or "", plan.get("status") or "active",
             int(plan.get("revision") or 1), float(plan.get("created") or now), now))
        con.execute("DELETE FROM task_items WHERE plan_id=?", (plan["plan_id"],))
        for i, task in enumerate(plan["tasks"]):
            con.execute(
                "INSERT INTO task_items(plan_id,task_id,ordinal,title,description,status,priority,"
                "executor,assignee,model,depends_on,input,outputs,history,acceptance,failure_reason,"
                "retry_count,max_retries,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (plan["plan_id"], task["id"], i, task["title"], task.get("description") or "",
                 task.get("status") or "pending", int(task.get("priority") or 0),
                 task.get("executor") or "main", task.get("assignee") or "",
                 task.get("model") or "",
                 _jdump(task.get("depends_on") or []), _jdump(task.get("input")),
                 _jdump(task.get("outputs") or []), _jdump(task.get("history") or []),
                 _jdump(task.get("acceptance") or []), task.get("failure_reason") or "",
                 int(task.get("retry_count") or 0), int(task.get("max_retries") or 0),
                 float(task.get("created") or now), float(task.get("updated") or now)))

    def create_plan(self, session_id: str, tasks: Iterable[Any], *, goal: str = "",
                    title: str = "", plan_id: str = "", replace: bool = False) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            raise TaskPlanError("session_id 不能为空")
        pid = _clean_id(plan_id, "plan-" + uuid.uuid4().hex[:10]) if plan_id else (
            "plan-" + uuid.uuid4().hex[:10])
        clean = normalize_tasks(tasks, plan_id=pid)
        recompute_statuses(clean, unblock=True)
        now = _now()
        with self._connect() as con:
            active = con.execute(
                "SELECT * FROM task_plans WHERE session_id=? AND status='active' ORDER BY updated DESC LIMIT 1",
                (sid,)).fetchone()
            if active and not replace:
                raise TaskPlanError(
                    f"会话已有活动计划 {active['plan_id']}；要调整用 task_replan，或 replace=true 新建")
            if active and replace:
                con.execute("UPDATE task_plans SET status='cancelled', updated=? WHERE plan_id=?",
                            (now, active["plan_id"]))
                old = con.execute("SELECT task_id FROM task_items WHERE plan_id=?",
                                  (active["plan_id"],)).fetchall()
                for row in old:
                    con.execute(
                        "UPDATE task_items SET status='cancelled', failure_reason=?, updated=? "
                        "WHERE plan_id=? AND task_id=? AND status NOT IN ('completed','cancelled')",
                        ("被新计划取代", now, active["plan_id"], row["task_id"]))
            for task in clean:
                task.setdefault("history", []).append(
                    {"time": now, "event": "created", "status": task.get("status")})
            plan = {"plan_id": pid, "session_id": sid, "goal": str(goal or "").strip(),
                    "title": str(title or "").strip(), "status": "active", "revision": 1,
                    "created": now, "updated": now, "tasks": clean}
            self._write_plan(con, plan)
            return self._load(con, pid) or plan

    def get_plan(self, session_id: str = "", plan_id: str = "") -> dict[str, Any] | None:
        with self._connect() as con:
            if plan_id:
                plan = self._load(con, _clean_id(plan_id, ""))
                if plan and session_id and plan["session_id"] != str(session_id):
                    return None
                return plan
            sid = str(session_id or "").strip()
            if not sid:
                return None
            row = con.execute(
                "SELECT plan_id FROM task_plans WHERE session_id=? AND status='active' "
                "ORDER BY updated DESC LIMIT 1", (sid,)).fetchone()
            if not row:
                row = con.execute(
                    "SELECT plan_id FROM task_plans WHERE session_id=? ORDER BY updated DESC LIMIT 1",
                    (sid,)).fetchone()
            return self._load(con, str(row["plan_id"])) if row else None

    def list_plans(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT plan_id FROM task_plans WHERE session_id=? ORDER BY updated DESC LIMIT ?",
                (str(session_id or ""), max(1, min(int(limit or 20), 100)))).fetchall()
            return [p for p in (self._load(con, str(r["plan_id"])) for r in rows) if p]

    def update_task(self, session_id: str, task_id: str, *, plan_id: str = "",
                    **changes: Any) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        with self._connect() as con:
            plan = self._load(con, self._find_plan_id(con, sid, plan_id))
            if not plan:
                raise TaskPlanError("当前会话没有任务计划")
            by_id = _by_id(plan["tasks"])
            task = by_id.get(str(task_id))
            if not task:
                raise TaskPlanError(f"没有任务 {task_id}")
            if "depends_on" in changes:
                deps = []
                for dep in _deps_of(changes["depends_on"]):
                    dep_id = str(dep).strip()
                    if dep_id.isdigit():
                        idx = int(dep_id) - 1
                        dep_id = plan["tasks"][idx]["id"] if 0 <= idx < len(plan["tasks"]) else ""
                    if dep_id not in by_id:
                        raise TaskPlanError(f"依赖不存在: {dep!r}")
                    if dep_id == task["id"]:
                        raise TaskPlanError("任务不能依赖自己")
                    deps.append(dep_id)
                task["depends_on"] = sorted(set(deps))
            for key in ("title", "description", "priority", "executor", "assignee", "model",
                        "input", "outputs", "acceptance", "failure_reason", "retry_count",
                        "max_retries"):
                if key in changes and changes[key] is not None:
                    if key == "executor":
                        task[key] = _norm_executor(changes[key])
                    elif key in ("priority", "retry_count", "max_retries"):
                        task[key] = int(changes[key])
                    elif key == "outputs":
                        task[key] = list(changes[key] or [])
                    elif key == "acceptance":
                        task[key] = list(changes[key] or [])
                    elif key in ("title", "description", "assignee", "model"):
                        task[key] = str(changes[key] or "")
                    else:
                        task[key] = changes[key]
            if "status" in changes:
                status = _norm_status(changes["status"])
                if status in ("running", "completed", "ready"):
                    by_id2 = _by_id(plan["tasks"])
                    bad = [d for d in task.get("depends_on") or []
                           if str(by_id2[d].get("status")) != "completed"]
                    if bad:
                        raise TaskPlanError(f"任务 {task_id} 的前置未完成: {bad}")
                if status == "failed":
                    task["retry_count"] = int(task.get("retry_count") or 0) + 1
                    task["failure_reason"] = str(changes.get("failure_reason") or
                                                 task.get("failure_reason") or "任务失败")
                task["status"] = status
                task.setdefault("history", []).append(
                    {"time": _now(), "event": "status", "status": status,
                     "detail": str(task.get("failure_reason") or "")[:500]})
            else:
                task.setdefault("history", []).append(
                    {"time": _now(), "event": "update", "detail": "字段更新"})
            task["updated"] = _now()
            validate_tasks(plan["tasks"])
            recompute_statuses(plan["tasks"], unblock=True)
            if task.get("status") in ("failed", "cancelled"):
                self._block_downstream(plan["tasks"], task["id"])
            if _plan_completed(plan["tasks"]):
                plan["status"] = "completed"
            self._write_plan(con, plan)
            return self._load(con, plan["plan_id"]) or plan

    def _find_plan_id(self, con: sqlite3.Connection, session_id: str, plan_id: str = "") -> str:
        if plan_id:
            return _clean_id(plan_id, "")
        row = con.execute(
            "SELECT plan_id FROM task_plans WHERE session_id=? AND status='active' "
            "ORDER BY updated DESC LIMIT 1", (session_id,)).fetchone()
        if not row:
            row = con.execute(
                "SELECT plan_id FROM task_plans WHERE session_id=? ORDER BY updated DESC LIMIT 1",
                (session_id,)).fetchone()
        return str(row["plan_id"]) if row else ""

    def note(self, session_id: str, task_id: str, event: str, detail: str = "",
             *, plan_id: str = "") -> dict[str, Any]:
        """追加一条审查/编排历史，不改变任务状态。"""
        with self._connect() as con:
            pid = self._find_plan_id(con, str(session_id or ""), plan_id)
            plan = self._load(con, pid)
            if not plan:
                raise TaskPlanError("当前会话没有任务计划")
            task = _by_id(plan["tasks"]).get(str(task_id))
            if not task:
                raise TaskPlanError(f"没有任务 {task_id}")
            task.setdefault("history", []).append(
                {"time": _now(), "event": str(event or "note")[:80],
                 "detail": str(detail or "")[:2000]})
            task["updated"] = _now()
            self._write_plan(con, plan)
            return self._load(con, pid) or plan

    @staticmethod
    def _block_downstream(tasks: list[dict[str, Any]], task_id: str) -> None:
        for child in downstream_ids(tasks, [task_id]):
            task = _by_id(tasks)[child]
            if task.get("status") not in ("completed", "cancelled", "running"):
                task["status"] = "blocked"
                task["failure_reason"] = f"前置任务 {task_id} 失败/取消"
                task["updated"] = _now()

    def cancel(self, session_id: str, *, task_id: str = "", plan_id: str = "") -> dict[str, Any]:
        with self._connect() as con:
            pid = self._find_plan_id(con, str(session_id or ""), plan_id)
            plan = self._load(con, pid)
            if not plan:
                raise TaskPlanError("当前会话没有任务计划")
            now = _now()
            if task_id:
                task = _by_id(plan["tasks"]).get(str(task_id))
                if not task:
                    raise TaskPlanError(f"没有任务 {task_id}")
                if task.get("status") == "completed":
                    raise TaskPlanError("已完成任务不能取消")
                task["status"] = "cancelled"
                task["failure_reason"] = "用户取消"
                task.setdefault("history", []).append(
                    {"time": now, "event": "cancelled", "status": "cancelled"})
                self._block_downstream(plan["tasks"], task["id"])
            else:
                plan["status"] = "cancelled"
                for task in plan["tasks"]:
                    if task.get("status") not in ("completed", "cancelled"):
                        task["status"] = "cancelled"
                        task["failure_reason"] = "计划取消"
                        task.setdefault("history", []).append(
                            {"time": now, "event": "plan_cancelled", "status": "cancelled"})
            self._write_plan(con, plan)
            return self._load(con, pid) or plan

    def resume(self, session_id: str, *, task_id: str = "", plan_id: str = "") -> dict[str, Any]:
        with self._connect() as con:
            pid = self._find_plan_id(con, str(session_id or ""), plan_id)
            plan = self._load(con, pid)
            if not plan:
                raise TaskPlanError("当前会话没有任务计划")
            now = _now()
            if task_id:
                task = _by_id(plan["tasks"]).get(str(task_id))
                if not task:
                    raise TaskPlanError(f"没有任务 {task_id}")
                if task.get("status") in ("failed", "cancelled", "blocked"):
                    task["status"] = "pending"
                    task["failure_reason"] = ""
                    task["updated"] = now
                    task.setdefault("history", []).append(
                        {"time": now, "event": "resumed", "status": "pending"})
                plan["status"] = "active"
                recompute_statuses(plan["tasks"], unblock=True)
            else:
                plan["status"] = "active"
                for task in plan["tasks"]:
                    if task.get("status") == "cancelled":
                        task["status"] = "pending"
                        task["failure_reason"] = ""
                        task.setdefault("history", []).append(
                            {"time": now, "event": "resumed", "status": "pending"})
                recompute_statuses(plan["tasks"], unblock=True)
            self._write_plan(con, plan)
            return self._load(con, pid) or plan

    def replan(self, session_id: str, updates: list[dict[str, Any]], *,
               plan_id: str = "") -> dict[str, Any]:
        """用补丁调整任务图；只重算受影响下游，不重做已完成任务。"""
        if not isinstance(updates, list) or not updates:
            raise TaskPlanError("updates 必须是非空数组")
        with self._connect() as con:
            pid = self._find_plan_id(con, str(session_id or ""), plan_id)
            plan = self._load(con, pid)
            if not plan:
                raise TaskPlanError("当前会话没有任务计划")
            old_tasks = [dict(t) for t in plan["tasks"]]
            by_id = _by_id(plan["tasks"])
            changed: list[str] = []
            for patch in updates:
                if not isinstance(patch, dict):
                    raise TaskPlanError("每个 update 必须是对象")
                tid = _clean_id(patch.get("task_id") or patch.get("id"), "")
                if patch.get("remove"):
                    if tid not in by_id:
                        raise TaskPlanError(f"没有任务 {tid}")
                    if by_id[tid].get("status") == "completed":
                        raise TaskPlanError(f"已完成任务 {tid} 不能删除")
                    if any(tid in (t.get("depends_on") or []) for t in plan["tasks"]):
                        raise TaskPlanError(f"任务 {tid} 仍有下游依赖，不能删除")
                    plan["tasks"] = [t for t in plan["tasks"] if t["id"] != tid]
                    by_id.pop(tid, None)
                    changed.append(tid)
                    continue
                if not tid:
                    raise TaskPlanError("update 缺少 task_id")
                if tid not in by_id:
                    raw_new = {k: v for k, v in patch.items() if k not in ("task_id", "id")}
                    raw_new["id"] = tid
                    # 新任务可能依赖已有任务；必须把现有任务一起交给归一化器
                    # 解析 ID，不能只校验这一条孤立任务。
                    merged = normalize_tasks(plan["tasks"] + [raw_new])
                    new_task = merged[-1]
                    plan["tasks"].append(new_task)
                    by_id[tid] = new_task
                    changed.append(tid)
                    continue
                task = by_id[tid]
                if "depends_on" in patch:
                    deps = []
                    for dep in _deps_of(patch["depends_on"]):
                        dep_id = str(dep).strip()
                        if dep_id.isdigit():
                            idx = int(dep_id) - 1
                            dep_id = plan["tasks"][idx]["id"] if 0 <= idx < len(plan["tasks"]) else ""
                        if dep_id not in by_id:
                            raise TaskPlanError(f"依赖不存在: {dep!r}")
                        if dep_id == tid:
                            raise TaskPlanError("任务不能依赖自己")
                        deps.append(dep_id)
                    task["depends_on"] = sorted(set(deps))
                for key in ("title", "description", "priority", "executor", "assignee", "model",
                            "acceptance", "input", "outputs"):
                    if key in patch and patch[key] is not None:
                        task[key] = patch[key]
                task["updated"] = _now()
                changed.append(tid)
            validate_tasks(plan["tasks"])
            recompute_statuses(plan["tasks"], unblock=True)
            affected = sorted(set(changed) | set(downstream_ids(old_tasks + plan["tasks"], changed)))
            plan["status"] = "completed" if _plan_completed(plan["tasks"]) else "active"
            plan["revision"] = int(plan.get("revision") or 1) + 1
            plan["updated"] = _now()
            self._write_plan(con, plan)
            out = self._load(con, pid) or plan
            out["affected"] = affected
            return out
