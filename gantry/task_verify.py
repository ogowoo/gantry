# -*- coding: utf-8 -*-
"""任务产物审查（Batch 2 的最小实现）。

这里不是 Batch 3 的完整验收引擎，只负责回答一个关键问题：
子 Agent 说完成了，但它声明的文件和最简单的验收条件真的成立吗？

支持：
- outputs 里的文件/目录存在且非空；
- acceptance: file_exists / file_contains / file_min_bytes / final_contains；
- 没有任何可机器检查条件时明确返回 verified=False（不能把“文本声称完成”当成已验证）。

Batch 3 会在此基础上增加命令、测试、API、格式和组合条件。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

MAX_READ_BYTES = 8 * 1024 * 1024


def _safe_path(root: Path, raw: str) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    p = Path(text)
    try:
        target = (root / p).resolve() if not p.is_absolute() else p.resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _check_outputs(task: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in task.get("outputs") or []:
        if isinstance(item, str):
            rel, needle = item, ""
        elif isinstance(item, dict):
            rel = str(item.get("path") or "")
            needle = str(item.get("contains") or "")
        else:
            continue
        target = _safe_path(root, rel)
        exists = bool(target and (target.is_file() or target.is_dir()))
        size = 0
        if exists and target and target.is_file():
            try:
                size = target.stat().st_size
            except OSError:
                exists = False
        ok = exists and (size > 0 if target and target.is_file() else True)
        if ok and needle and target and target.is_file():
            ok = needle in _read_text(target)
        checks.append({"name": f"output:{rel}", "ok": ok,
                       "detail": f"exists={exists} bytes={size}",
                       "path": rel})
    return checks


def _check_acceptance(task: dict[str, Any], result: dict[str, Any],
                      root: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    content = str(result.get("content") or "")
    for idx, raw in enumerate(task.get("acceptance") or [], 1):
        if isinstance(raw, str):
            # 自然语言验收无法确定性执行；不冒充已验证。
            checks.append({"name": f"acceptance:{idx}", "ok": True,
                           "detail": "manual: " + raw[:200], "manual": True})
            continue
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "").strip().lower()
        if kind == "file_exists":
            rel = str(raw.get("path") or "")
            target = _safe_path(root, rel)
            checks.append({"name": f"acceptance:{idx}:file_exists",
                           "ok": bool(target and target.exists()),
                           "detail": rel, "path": rel})
        elif kind == "file_contains":
            rel = str(raw.get("path") or "")
            target = _safe_path(root, rel)
            needle = str(raw.get("text") or "")
            ok = bool(target and target.is_file() and needle in _read_text(target))
            checks.append({"name": f"acceptance:{idx}:file_contains",
                           "ok": ok, "detail": rel, "path": rel})
        elif kind == "file_min_bytes":
            rel = str(raw.get("path") or "")
            minimum = int(raw.get("bytes") or 1)
            target = _safe_path(root, rel)
            size = target.stat().st_size if target and target.is_file() else 0
            checks.append({"name": f"acceptance:{idx}:file_min_bytes",
                           "ok": size >= minimum,
                           "detail": f"{size} >= {minimum}", "path": rel})
        elif kind == "final_contains":
            needle = str(raw.get("text") or "")
            checks.append({"name": f"acceptance:{idx}:final_contains",
                           "ok": bool(needle) and needle in content,
                           "detail": f"needle={needle!r}"})
        elif kind in ("tests_pass", "syntax_pass"):
            verification = result.get("verification") or {}
            key = "tests_passed" if kind == "tests_pass" else "syntax_passed"
            checks.append({"name": f"acceptance:{idx}:{kind}",
                           "ok": bool(verification.get(key)),
                           "detail": f"child verification={verification}"})
        else:
            checks.append({"name": f"acceptance:{idx}", "ok": True,
                           "detail": f"unsupported check type: {kind}", "manual": True})
    return checks


def review_task(task: dict[str, Any], result: dict[str, Any], workspace: str | Path) -> dict[str, Any]:
    """检查一次子 Agent 结果；返回 {ok, verified, checks, reason}。"""
    root = Path(workspace).resolve()
    checks: list[dict[str, Any]] = []
    error = str(result.get("error") or "").strip()
    if error:
        return {"ok": False, "verified": False,
                "checks": [{"name": "agent_error", "ok": False, "detail": error}],
                "reason": error[:1000]}
    checks.extend(_check_outputs(task, root))
    checks.extend(_check_acceptance(task, result, root))
    failed = [c for c in checks if not c["ok"]]
    manual = [c for c in checks if c.get("manual")]
    verified = bool(checks) and not any(c.get("manual") for c in checks)
    content = str(result.get("content") or "").strip()
    if not checks and not content:
        return {"ok": False, "verified": False, "checks": checks,
                "reason": "子 Agent 没有返回内容，也没有可检查的产物"}
    reason = ""
    if failed:
        reason = "; ".join(f"{c['name']}: {c.get('detail','')}" for c in failed)[:1500]
    elif not verified:
        reason = "没有可确定性验证的产物/验收条件；只能确认文本结果"
    return {"ok": not failed, "verified": verified, "checks": checks,
            "failed": [c["name"] for c in failed], "manual": len(manual),
            "reason": reason}
