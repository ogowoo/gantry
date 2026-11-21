# -*- coding: utf-8 -*-
"""轨迹级回归评测：把"Agent 该怎么做"固化成可重跑的断言。

与单元测试的分工：单测验函数与解析逻辑；本脚本验**真实模型跑真实任务**时
的行为——工具序列、步骤数、文件结果、是否触发验证门、是否用了批量/记忆。
线上真实失败应固化成这里的一条 case（incident-to-eval）。

用法:
    python scripts/eval.py                          # 用 config 的默认模型（Copilot 网页）
    python scripts/eval.py --model deepseek/deepseek-chat   # 换成 API 提供商（更快）
    python scripts/eval.py --only file-write,memory-write   # 只跑指定任务
    python scripts/eval.py --keep                   # 保留评测会话，便于回看时间线

输出: data/evals/report-<时间戳>.json（逐条断言结果）+ 终端汇总表。
退出码: 任一条失败即 1（可直接进 CI）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from health import check_session  # noqa: E402  同目录脚本，逐会话做事件核对
WS = BASE / "workspace"
TASKS = BASE / "tests" / "evals" / "tasks.json"
REPORT_DIR = BASE / "data" / "evals"
API = os.environ.get("CBP_API", "http://127.0.0.1:8765")


def _api_token() -> str:
    token = os.environ.get("CBP_TOKEN") or os.environ.get("GANTRY_API_TOKEN") or ""
    if token:
        return token.strip()
    try:
        return (BASE / "data" / "api_token").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def check_task(task: dict, steps: list[dict], answer: str, memory_items: list[dict],
               sid: str = "") -> list[dict]:
    """逐条断言；返回 [{name, ok, detail}]。断言全部是确定性的，不引入 LLM judge。"""
    a = task.get("assert") or {}
    out: list[dict] = []
    tools = [s.get("tool") for s in steps if s.get("tool")]
    statuses = [s.get("status") for s in steps]

    def add(name: str, ok: bool, detail: str = ""):
        out.append({"name": name, "ok": bool(ok), "detail": detail})

    if "tools_used_any" in a:
        want = a["tools_used_any"]
        hit = [t for t in tools if t in want]
        add("tools_used_any", bool(hit), f"期望其一 {want}，实际 {tools}")
    if "tools_used_all" in a:
        missing = [t for t in a["tools_used_all"] if t not in tools]
        add("tools_used_all", not missing, f"缺失 {missing}")
    if "tools_forbidden" in a:
        bad = [t for t in a["tools_forbidden"] if t in tools]
        add("tools_forbidden", not bad, f"出现了禁止工具 {bad}")
    if "max_steps" in a:
        add("max_steps", len(steps) <= a["max_steps"],
            f"步数 {len(steps)} <= {a['max_steps']}")
    if "min_tools" in a:
        add("min_tools", len(tools) >= int(a["min_tools"]), f"工具调用 {len(tools)}")
    if a.get("no_validation_gate"):
        n = sum(1 for s in statuses if s == "validation_gate")
        add("no_validation_gate", n == 0, f"验证门拦截 {n} 次（说明提前 final）")
    if a.get("batch_used"):
        b = [s.get("batch_of") for s in steps if s.get("batch_of")]
        add("batch_used", bool(b), "没有出现批量动作")
    if a.get("task_planned"):
        add("task_planned", "task_plan" in tools, "没有建立任务图")
    if a.get("task_run_used"):
        add("task_run_used", "task_run" in tools, "没有调用 task_run")
    if "subagents_used_any" in a:
        want = a["subagents_used_any"]
        hit = [t for t in tools if t in want]
        add("subagents_used_any", bool(hit), f"实际 {tools}")
    if "max_no_progress" in a:
        n = sum(1 for s in statuses if s in ("no_progress", "no_progress_warning"))
        add("max_no_progress", n <= int(a["max_no_progress"]), f"无进展 {n} 次")
    for name in a.get("files_exist", []):
        p = WS / name
        add(f"file_exists:{name}", p.is_file(), str(p))
    for name in a.get("files_absent", []):
        p = WS / name
        add(f"file_absent:{name}", not p.exists(), str(p))
    for name, needle in (a.get("file_contains") or {}).items():
        p = WS / name
        ok = p.is_file() and needle in p.read_text(encoding="utf-8", errors="replace")
        add(f"file_contains:{name}", ok, f"需包含 {needle!r}")
    if "memory_contains" in a:
        key = a["memory_contains"]
        # 必须由本会话写入：否则历史同名条目会让"幻觉式完成"蒙混过关
        mine = [m for m in memory_items
                if key in (m.get("text") or "") and (not sid or m.get("source_session") == sid)]
        add("memory_contains", bool(mine),
            f"本会话({sid or '-'})未写入含 {key!r} 的记忆（本次可见 {len(memory_items)} 条）")
    if "final_matches" in a:
        import re as _re
        add("final_matches", bool(_re.search(a["final_matches"], answer or "")),
            f"最终答复未匹配 {a['final_matches']!r}（实际: {(answer or '')[:80]!r}）")
    if "final_contains" in a:
        add("final_contains", a["final_contains"] in (answer or ""), "最终答复未包含关键字")
    return out


def compute_metrics(results: list[dict]) -> dict:
    """从逐任务结果汇总可比较指标；缺失的指标明确为 None，不伪造。"""
    total = len(results)
    passed = sum(1 for r in results if r.get("ok"))
    checks = [c for r in results for c in (r.get("checks") or [])]
    passed_checks = sum(1 for c in checks if c.get("ok"))
    steps = sum(int(r.get("steps") or 0) for r in results)
    tools = sum(len(r.get("tools") or []) for r in results)
    seconds = sum(float(r.get("seconds") or 0) for r in results)
    retries = sum(int(r.get("attempts") or 0) for r in results)
    statuses = [s for r in results for s in (r.get("statuses") or [])]
    no_progress = sum(1 for s in statuses if s in ("no_progress", "no_progress_warning"))
    sub_calls = sum(int(r.get("subagent_calls") or 0) for r in results)
    sub_ok = sum(int(r.get("subagent_ok") or 0) for r in results)
    sub_rework = sum(int(r.get("subagent_rework") or 0) for r in results)
    tokens = sum(int((r.get("usage") or {}).get("total_tokens") or 0) for r in results)
    compacted = sum(1 for r in results if r.get("compacted"))
    compacted_ok = sum(1 for r in results if r.get("compacted") and r.get("ok"))
    return {
        "tasks": total, "completed": passed,
        "completion_rate": round(passed / total, 4) if total else 0.0,
        "acceptance_checks": len(checks), "acceptance_passed": passed_checks,
        "acceptance_rate": round(passed_checks / len(checks), 4) if checks else None,
        "avg_seconds": round(seconds / total, 2) if total else 0.0,
        "avg_steps": round(steps / total, 2) if total else 0.0,
        "total_tools": tools, "avg_tools": round(tools / total, 2) if total else 0.0,
        "avg_retries": round(retries / total, 2) if total else 0.0,
        "no_progress": no_progress,
        "subagent_calls": sub_calls, "subagent_success": sub_ok,
        "subagent_success_rate": round(sub_ok / sub_calls, 4) if sub_calls else None,
        "subagent_rework": sub_rework,
        "subagent_rework_rate": round(sub_rework / sub_calls, 4) if sub_calls else None,
        "total_tokens": tokens, "avg_tokens": round(tokens / total, 2) if total else 0.0,
        "compacted_tasks": compacted, "compaction_recovery": compacted_ok,
        "compaction_recovery_rate": round(compacted_ok / compacted, 4) if compacted else None,
        "cost_usd": None,
    }


def _load_previous_report(directory: Path, current: Path) -> dict | None:
    try:
        reports = sorted((p for p in directory.glob("report-*.json") if p != current),
                         key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return None
    if not reports:
        return None
    try:
        return json.loads(reports[0].read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="默认用服务端 config 的 default_model")
    ap.add_argument("--only", default="", help="逗号分隔的任务 id")
    ap.add_argument("--keep", action="store_true", help="保留评测会话")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--task-timeout", type=float, default=300,
                    help="单条任务上限秒数，超时即取消该会话（默认 300）")
    ap.add_argument("--wait-idle", type=float, default=120,
                    help="每条任务前等待服务空闲的上限秒数")
    ap.add_argument("--compare", action="store_true",
                    help="与上一份 report 比较指标并输出 delta")
    ap.add_argument("--warmup-timeout", type=float, default=600,
                    help="首个 Copilot 任务前等待浏览器/Copilot 就绪的上限秒数（按需启动的首次代价，"
                         "不计入单任务超时）")
    args = ap.parse_args()

    spec = json.loads(TASKS.read_text(encoding="utf-8"))
    tasks = spec["tasks"]
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        tasks = [t for t in tasks if t["id"] in want]
    if not tasks:
        print("没有匹配的任务")
        return 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    WS.mkdir(exist_ok=True)
    results = []
    failed = 0
    stamp = time.strftime("%Y%m%d-%H%M%S")

    with httpx.Client(base_url=API, timeout=args.timeout,
                      headers={"X-Gantry-Token": _api_token()} if _api_token() else {}) as c:
        health = c.get("/health").json()
        model = args.model or health.get("default_model") or "copilot-auto"
        print(f"评测模型: {model}  页面: {str(health.get('page_url'))[:60]}\n")

        def wait_idle(limit: float) -> bool:
            """等上一条任务释放全局锁，避免把后续任务打成 0 步假失败。"""
            end = time.time() + limit
            while time.time() < end:
                try:
                    if not c.get("/health", timeout=10).json().get("busy"):
                        return True
                except Exception:
                    pass
                time.sleep(3)
            return False

        # 预热：lazy 模式下浏览器按需启动 + 企业租户 SSO 可能需要数分钟；
        # 这段时间不应算进某一条任务的超时（否则首条任务无谓失败）。
        # 预热只对"走 Copilot 网页"的模型有意义：提供商路径（deepseek/…）是一次 HTTP
        # 调用，不需要浏览器。以前无条件预热，提供商评测会白等 600 秒超时才开跑。
        _needs_browser = "/" not in str(model)
        if _needs_browser and not c.get("/health", timeout=10).json().get("copilot_ready"):
            print(f"预热中：等待 Copilot 就绪（最多 {args.warmup_timeout:.0f} 秒）…")
            try:
                c.post("/v1/agent/browser/retry", timeout=30)
            except Exception:
                pass
            end = time.time() + args.warmup_timeout
            while time.time() < end:
                try:
                    if c.get("/health", timeout=10).json().get("copilot_ready"):
                        print("Copilot 已就绪，开始评测\n")
                        break
                except Exception:
                    pass
                time.sleep(5)
            else:
                print("预热超时（可能需要人工在自动化窗口登录）；仍继续，首条任务可能失败\n")
        elif not _needs_browser:
            print("提供商路径：跳过 Copilot 预热（不需要浏览器）\n")

        pending = list(tasks)
        attempts: dict[str, int] = {}
        while pending:
            t = pending.pop(0)
            # 会话 ID 带运行戳：固定 ID 会复用到历史会话的 cancelled/archived 状态，
            # 导致 runner 看到取消标志直接退出（200 + 0 步的假失败）。
            sid = f"eval-{t['id']}-{stamp}"
            t0 = time.time()
            try:
                c.delete(f"/v1/agent/sessions/{sid}", params={"force_local": "true"}, timeout=20)
            except Exception:
                pass
            # 清理上一轮评测留下的产物：否则后续任务的 run_unittest 会踩到脏文件
            # （实测 gate-limit 因 tests/test_eval_mod.py 引用已删除的 eval_mod 而
            #  花 10+ 步去补模块，步数从 4 涨到 21，纯属评测卫生问题）。
            patterns = ("eval_*", "tests/test_eval_*", "tests/eval_*", ".spill")
            for pat in patterns:
                for old in WS.glob(pat):
                    try:
                        if old.is_file():
                            old.unlink()
                    except Exception:
                        pass
            tdir = WS / "tests"
            try:
                if tdir.is_dir() and not any(tdir.iterdir()):
                    tdir.rmdir()
            except Exception:
                pass
            if not wait_idle(args.wait_idle):
                results.append({"id": t["id"], "ok": False, "seconds": 0, "steps": 0, "tools": [],
                                "blocked": True, "checks": [
                                    {"name": "service_idle", "ok": False,
                                     "detail": "等待服务空闲超时，上一条任务未释放锁"}], "error": None})
                print(f"[BLOCK] {t['id']:<22} 服务忙，跳过")
                failed += 1
                continue
            try:
                r = c.post("/v1/agent/completions", json={
                    "model": model, "session_id": sid, "stream": False,
                    "messages": [{"role": "user", "content": t["task"]}]},
                    timeout=args.task_timeout)
                d = r.json() if r.status_code == 200 else {}
                if r.status_code in (409, 503):
                    d["_blocked"] = f"HTTP {r.status_code}: {r.text[:120]}"
            except Exception as exc:
                d = {"_error": str(exc)}
                # 超时/异常：主动取消该会话，避免继续占用全局锁
                try:
                    c.post(f"/v1/agent/sessions/{sid}/actions/cancel", timeout=20)
                except Exception:
                    pass
            steps = d.get("agent_steps") or []
            answer = ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "")
            try:
                mem = c.get("/v1/agent/memory", params={"scope": "all", "limit": 200}).json()
                mem_items = mem.get("items", [])
            except Exception:
                mem_items = []
            # ── 事件核对（P2-10）：每条评测任务都顺带成为"日志能否重建状态"的探针。
            # 必须在本任务会话被删除**之前**做，否则查不到数据。
            hc: dict = {}
            try:
                con = sqlite3.connect(BASE / "data" / "agent.db")
                con.row_factory = sqlite3.Row
                hc = check_session(con, sid)
                con.close()
            except Exception as exc:
                hc = {"error": f"{type(exc).__name__}: {exc}"}

            checks = check_task(t, steps, answer, mem_items, sid)
            # 投影≠列属**致命**问题（说明事件流不足以重建状态）；覆盖缺口只告警
            if hc.get("problems"):
                checks.append({"name": "projection_consistency", "ok": False,
                               "detail": "; ".join(hc["problems"][:3])})
            else:
                checks.append({"name": "projection_consistency", "ok": True,
                               "detail": f"事件 {hc.get('events', 0)} 条，与列一致"})
            ok = all(x["ok"] for x in checks)
            failed += 0 if ok else 1
            if d.get("_blocked"):
                checks.insert(0, {"name": "service_idle", "ok": False, "detail": d["_blocked"]})
                ok = False
            sub_calls = sub_ok = 0
            statuses = [x.get("status") for x in steps]
            for st in steps:
                if st.get("tool") not in ("subagent", "subagent_many"):
                    continue
                r = st.get("result") or {}
                if st.get("tool") == "subagent_many":
                    sub_ok += int(r.get("succeeded") or 0)
                    sub_calls += int(r.get("succeeded") or 0) + int(r.get("failed") or 0)
                else:
                    sub_calls += 1
                    sub_ok += 1 if st.get("status") == "ok" else 0
            results.append({"id": t["id"], "ok": ok, "seconds": round(time.time() - t0, 1),
                            "steps": len(steps), "tools": [s.get("tool") for s in steps if s.get("tool")],
                            "final": (answer or "")[:400],
                            "step_log": [{"step": x.get("step"), "tool": x.get("tool") or x.get("status"),
                                          "args": json.dumps(x.get("arguments") or {}, ensure_ascii=False)[:160],
                                          "error": (x.get("error") or "")[:160]}
                                         for x in steps[-12:]],
                            "checks": checks, "error": d.get("_error") or d.get("_blocked"),
                            "events": hc.get("events"), "coverage_gaps": hc.get("coverage_gaps") or [],
                            "legacy_no_events": bool(hc.get("legacy")),
                            "attempts": attempts.get(t["id"], 0),
                            "statuses": statuses,
                            "usage": d.get("usage") or {},
                            "subagent_calls": sub_calls,
                            "subagent_ok": sub_ok,
                            "subagent_rework": 0,
                            "compacted": bool(d.get("compacted") or d.get("compaction"))})
            if not ok and attempts.get(t["id"], 0) < int(t.get("retries") or 0):
                attempts[t["id"]] = attempts.get(t["id"], 0) + 1
                print(f"[RETRY] {t['id']} 第 {attempts[t['id']] + 1} 次尝试"
                      f"（模型方差，失败后重试一次）")
                pending.append(t)
                continue
            mark = "PASS" if ok else "FAIL"
            bad = [x["name"] for x in checks if not x["ok"]]
            extra = f"  失败断言: {', '.join(bad)}" if bad else ""
            if bad and len(steps) == 0:
                extra += f" | final: {(answer or '')[:110]!r}"
            if hc.get("coverage_gaps"):
                extra += f"  ⚠ 事件覆盖: {'; '.join(hc['coverage_gaps'][:1])}"
            print(f"[{mark}] {t['id']:<22} {round(time.time()-t0,1):>6}s 步数 {len(steps):<3}{extra}")
            if not args.keep:
                try:
                    c.delete(f"/v1/agent/sessions/{sid}", params={"force_local": "true"})
                except Exception:
                    pass

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = REPORT_DIR / f"report-{stamp}.json"
    metrics = compute_metrics(results)
    previous = _load_previous_report(REPORT_DIR, out) if args.compare else None
    comparison = None
    if previous and isinstance(previous.get("metrics"), dict):
        before = previous["metrics"]
        comparison = {}
        for key, value in metrics.items():
            old = before.get(key)
            if isinstance(value, (int, float)) and isinstance(old, (int, float)):
                comparison[key] = {"before": old, "after": value,
                                   "delta": round(value - old, 4)}
            else:
                comparison[key] = {"before": old, "after": value, "delta": None}
    out.write_text(json.dumps({"model": model, "when": stamp, "metrics": metrics,
                               "comparison": comparison, "results": results},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    no_events = sum(1 for r in results if r.get("legacy_no_events"))
    mism = sum(1 for r in results for c in r["checks"]
               if c["name"] == "projection_consistency" and not c["ok"])
    gaps = sum(1 for r in results if r.get("coverage_gaps"))
    print(f"\n汇总: {len(results) - failed}/{len(results)} 通过   报告: {out}")
    print(f"指标: 完成率 {metrics['completion_rate']:.0%}  "
          f"验收率 {metrics['acceptance_rate'] if metrics['acceptance_rate'] is not None else 'n/a'}  "
          f"平均 {metrics['avg_seconds']}s/{metrics['avg_steps']} 步  "
          f"重试 {metrics['avg_retries']} 无进展 {metrics['no_progress']}")
    if comparison:
        changed = [k for k, v in comparison.items() if v.get("delta") not in (None, 0)]
        print("对比上一份报告: " + ("；".join(
            f"{k} {comparison[k]['before']}→{comparison[k]['after']}" for k in changed[:8])
            or "无变化"))
    print(f"事件核对: 投影≠列 {mism} 例，覆盖告警 {gaps} 例，无事件 {no_events} 例")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
