# -*- coding: utf-8 -*-
"""显式扇出：调用方直接给 N 个独立任务，各交给一个子代理。

“不经模型判断”只指**任务拆分**：弱模型经常选择自己串行做完，所以给用户/CLI 一个
显式入口，不依赖模型是否愿意拆；每个子任务的执行仍由子代理模型完成。

需要模型自主拆任务时用 `split="model"` + `goal=...`；需要模型自主决定是否扇出时用
`subagent_many` 工具。

    POST /v1/agent/fanout {"tasks": ["..."], "model": "..."}
    POST /v1/agent/fanout {"goal": "...", "split": "model"}
    gantry fanout --task "..." --task "..."

任务可以带依赖：
    [{"task": "A"}, {"task": "B", "depends_on": [1]}]

安全开关：
- `isolate_workspace=True`：每个子任务一份工作区副本，避免并发写同一文件；
- `merge=True`：任务成功后把副本里的文件合并回父工作区（后完成者覆盖）；
- `serial=True`：强制顺序执行；
- `summary=True`：跑完后让模型把结果汇总到父会话。
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from . import bridge as b

_MAX_TASKS = 8
_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "node_modules", ".fanout", "__pycache__", ".pytest_cache")


def _normalize(tasks: list | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in tasks or []:
        if isinstance(raw, str):
            task, label, deps = raw.strip(), "", []
        elif isinstance(raw, dict):
            task = str(raw.get("task") or raw.get("prompt") or "").strip()
            label = str(raw.get("description") or raw.get("label") or "").strip()
            deps = raw.get("depends_on") or raw.get("after") or []
            if isinstance(deps, (str, int)):
                deps = [deps]
        else:
            raise b.ToolError("tasks 里每项应为字符串或 {task, description, depends_on}")
        if not task:
            continue
        key = task.casefold()
        if key in seen:
            raise b.ToolError(f"任务重复：{task[:80]}")
        seen.add(key)
        out.append({"task": task, "description": label, "depends_on": list(deps)})
    return out


def _resolve_deps(items: list[dict[str, Any]]) -> None:
    """把 depends_on 里的 1-based 序号 / label / task 文本解析成 0-based 下标。"""
    by_name: dict[str, int] = {}
    for i, it in enumerate(items):
        for name in (it.get("description"), it.get("task")):
            name = str(name or "").strip().casefold()
            if name:
                by_name[name] = i
    for i, it in enumerate(items):
        deps: list[int] = []
        for raw in it.get("depends_on") or []:
            if isinstance(raw, int):
                idx = raw - 1
            else:
                s = str(raw).strip()
                if s.isdigit():
                    idx = int(s) - 1
                else:
                    idx = by_name.get(s.casefold(), -1)
            if idx < 0 or idx >= len(items):
                raise b.ToolError(f"任务 {i + 1} 的依赖无效：{raw!r}")
            if idx == i:
                raise b.ToolError(f"任务 {i + 1} 不能依赖自己")
            deps.append(idx)
        it["deps"] = sorted(set(deps))


def _waves(items: list[dict[str, Any]]) -> list[list[int]]:
    done: set[int] = set()
    remaining = set(range(len(items)))
    waves: list[list[int]] = []
    while remaining:
        ready = [i for i in sorted(remaining)
                 if all(d in done for d in items[i].get("deps") or [])]
        if not ready:
            raise b.ToolError("depends_on 有环或依赖无法满足")
        waves.append(ready)
        done.update(ready)
        remaining -= set(ready)
    return waves


def _parse_tasks(text: str) -> list[Any]:
    s = str(text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    raw = m.group(1) if m else s
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        raise b.ToolError("模型没有返回 JSON 数组（无法拆分任务）")
    try:
        data = json.loads(raw[start:end + 1])
    except Exception as exc:                    # noqa: BLE001
        raise b.ToolError(f"模型返回的拆分结果不是合法 JSON：{str(exc)[:120]}")
    if isinstance(data, dict):
        data = data.get("tasks") or []
    if not isinstance(data, list):
        raise b.ToolError("模型返回的拆分结果不是数组")
    return data


async def _split_goal(goal: str, model: str) -> list[Any]:
    prompt = (
        "你是任务规划器。把下面的目标拆成 2-8 个**相互独立、可并行**的子任务。\n"
        "只输出 JSON 数组，不要执行任务，不要输出解释。每项格式：\n"
        '{"task": "具体任务", "description": "短标签"}\n\n'
        f"目标：{goal}"
    )
    out = await b.run_subagent(prompt, "任务拆分", model, 1)
    err = out.get("error")
    if err:
        raise b.ToolError(f"模型拆分失败：{err}")
    return _parse_tasks(out.get("content") or "")


def _copy_workspace(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_COPY_IGNORE)


def _merge_back(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src)
        if rel.parts and rel.parts[0] in (".git", ".venv", "node_modules", ".fanout"):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)


async def _summarize(parent_sid: str, items: list[dict], results: list[dict],
                     model: str) -> str:
    lines = []
    for r in results:
        status = "成功" if r.get("ok") else "失败"
        body = r.get("content") or r.get("error") or ""
        lines.append(f"{r['index']}. [{status}] {r['task']}\n{body[:1500]}")
    prompt = (
        "下面是多个子任务的执行结果。请给父会话写一段简洁汇总（中文，6 行以内）："
        "说明完成了什么、失败/风险、下一步建议。不要重复大段原文。\n\n"
        + "\n\n".join(lines)
    )
    out = await b.run_subagent(prompt, "扇出汇总", model, 1)
    return str(out.get("content") or out.get("error") or "")


async def fanout(tasks: list | None = None, model: str = "", workspace: str = "",
                 max_steps: int = 40, parent_title: str = "", *,
                 isolate_workspace: bool = False, merge: bool = False,
                 keep_workspace: bool = False,
                 summary: bool = False, serial: bool = False,
                 goal: str = "", split: str = "none") -> dict[str, Any]:
    """建一个父会话，把每个任务交给一个子代理，结果写回父会话。"""
    if merge and not isolate_workspace:
        raise b.ToolError("merge 需要同时开启 isolate_workspace")
    if not tasks and goal and str(split).lower() == "model":
        tasks = await _split_goal(goal, model or b.DEFAULT_MODEL)
    items = _normalize(tasks)
    if not items:
        raise b.ToolError("tasks 不能为空")
    if len(items) > _MAX_TASKS:
        raise b.ToolError(f"一次最多 {_MAX_TASKS} 个子任务（收到 {len(items)} 个）")
    _resolve_deps(items)

    base_root = Path(str(workspace or b.task_tools().root)).resolve()
    if not base_root.exists():
        raise b.ToolError(f"工作目录不存在: {base_root}")
    base_reg = b.task_tools()
    if str(workspace or "").strip():
        base_reg = b.task_tools().clone()
        base_reg.set_root(base_root, False)
        base_root = Path(str(base_reg.root)).resolve()

    parent_sid = "fan-" + uuid.uuid4().hex[:10]
    title = (parent_title or items[0]["task"] or "扇出任务")[:120]
    b.AGENT_SESSIONS.create(parent_sid, title, str(base_root),
                            b.normalize_model_soft(model or b.DEFAULT_MODEL))
    b.AGENT_SESSIONS.patch(parent_sid, description=f"扇出 {len(items)} 个子任务")
    b.AGENT_SESSIONS.add_message(parent_sid, "user",
                                 "扇出任务：\n" + "\n".join(
                                     f"{i}. {it['task']}" for i, it in enumerate(items, 1)),
                                 0)

    serial = bool(serial)
    waves = _waves(items)
    model_id = model or b.DEFAULT_MODEL
    parallel = b.model_is_provider(model_id) and len(items) > 1 and not serial
    cap = b.subagent_parallel() if parallel else 1
    sem = asyncio.Semaphore(cap)
    results: list[dict[str, Any]] = []
    by_index: dict[int, dict[str, Any]] = {}
    prev_active = b.ACTIVE_SESSION_ID
    tok_sid = b._SESSION_CTX.set(parent_sid)
    b.ACTIVE_SESSION_ID = parent_sid
    t0 = time.time()

    async def run_one(idx: int, item: dict) -> dict:
        async with sem:
            task_root = base_root
            reg = base_reg
            if isolate_workspace:
                task_root = base_root / ".fanout" / parent_sid / f"task-{idx + 1}"
                try:
                    _copy_workspace(base_root, task_root)
                except Exception as exc:         # noqa: BLE001
                    return {"index": idx + 1, "task": item["task"][:120], "ok": False,
                            "error": f"复制工作区失败: {str(exc)[:160]}"}
                reg = base_reg.clone()
                try:
                    reg.set_root(task_root, False)
                except Exception as exc:         # noqa: BLE001
                    return {"index": idx + 1, "task": item["task"][:120], "ok": False,
                            "error": f"切换工作区失败: {str(exc)[:160]}"}
            tok = b._TOOLS_CTX.set(reg)
            try:
                out = await b.run_subagent(item["task"], item["description"],
                                           model_id, max_steps)
            except Exception as exc:             # noqa: BLE001
                return {"index": idx + 1, "task": item["task"][:120], "ok": False,
                        "error": f"{type(exc).__name__}: {exc}"}
            finally:
                b._TOOLS_CTX.reset(tok)
            err = out.get("error")
            rec = {"index": idx + 1, "task": item["task"][:120], "ok": not err,
                   "agent_id": out.get("agent_id"), "steps": out.get("steps"),
                   "content": out.get("content") or "",
                   **({"error": err} if err else {})}
            if isolate_workspace and merge and not err:
                try:
                    _merge_back(task_root, base_root)
                    rec["merged"] = True
                except Exception as exc:         # noqa: BLE001
                    rec["merge_error"] = str(exc)[:160]
            return rec

    try:
        for wave in waves:
            runnable: list[int] = []
            for idx in wave:
                deps = items[idx].get("deps") or []
                bad = [d for d in deps if not by_index.get(d, {}).get("ok")]
                if bad:
                    rec = {"index": idx + 1, "task": items[idx]["task"][:120],
                           "ok": False, "error": f"依赖失败/跳过：{[d + 1 for d in bad]}",
                           "skipped": True}
                    results.append(rec)
                    by_index[idx] = rec
                else:
                    runnable.append(idx)
            if not runnable:
                continue
            if parallel and len(runnable) > 1:
                recs = await asyncio.gather(*(run_one(i, items[i]) for i in runnable))
            else:
                recs = []
                for i in runnable:
                    recs.append(await run_one(i, items[i]))
            for rec in recs:
                by_index[rec["index"] - 1] = rec
                results.append(rec)
            for rec in sorted(recs, key=lambda r: r["index"]):
                if rec["ok"]:
                    b.AGENT_SESSIONS.add_message(
                        parent_sid, "assistant",
                        f"### 子任务 {rec['index']}\n{rec.get('content') or ''}", 0)
                else:
                    b.AGENT_SESSIONS.add_message(
                        parent_sid, "assistant",
                        f"### 子任务 {rec['index']} 失败\n{rec.get('error')}", 0)
    finally:
        b.ACTIVE_SESSION_ID = prev_active
        try:
            b._SESSION_CTX.reset(tok_sid)
        except Exception:
            pass
        if isolate_workspace and not keep_workspace:
            try:
                shutil.rmtree(base_root / ".fanout" / parent_sid, ignore_errors=True)
            except Exception:                    # noqa: BLE001
                pass

    results.sort(key=lambda r: r["index"])
    summary_text = ""
    if summary:
        tok = b._SESSION_CTX.set(parent_sid)
        try:
            summary_text = await _summarize(parent_sid, items, results, model_id)
            if summary_text:
                b.AGENT_SESSIONS.add_message(parent_sid, "assistant",
                                             f"### 汇总\n{summary_text}", 0)
        except Exception as exc:                 # noqa: BLE001
            summary_text = ""
            b.log(f"扇出汇总失败（不影响结果）：{exc}")
        finally:
            b._SESSION_CTX.reset(tok)

    ok = [r for r in results if r.get("ok")]
    b.AGENT_SESSIONS.patch(parent_sid, state="completed" if ok else "failed")
    return {"session_id": parent_sid, "requested": len(items), "succeeded": len(ok),
            "failed": len(results) - len(ok), "seconds": round(time.time() - t0, 1),
            "parallel": bool(parallel), "concurrency": cap, "serial": serial,
            "isolated": bool(isolate_workspace),
            "merged": any(bool(r.get("merged")) for r in results),
            "split": str(split), "waves": waves,
            "model": model_id, "workspace": str(base_root),
            "summary": summary_text, "results": results}
