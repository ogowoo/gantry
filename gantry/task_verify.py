# -*- coding: utf-8 -*-
"""任务产物验收：确定性检查 + 可选的独立验证 Agent。

设计边界：
- **确定性优先**：文件、格式、命令、测试、API 的结果由本模块真实执行/读取；
- **不信任文字总结**：子 Agent 的 content 只用于 final_contains，不用于证明文件存在；
- **可选独立验证**：`independent_review` 让另一个子 Agent 读产物并给 JSON 结论，
  但它只作为补充证据，确定性检查失败时不会因“另一个 Agent 说没问题”而放行；
- 命令/API 检查默认关闭，调用方必须显式 allow_commands；高风险任务没有确定性
  证据时直接失败。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

MAX_READ_BYTES = 8 * 1024 * 1024
READ_SUFFIXES = {".txt", ".md", ".json", ".py", ".js", ".ts", ".html", ".css",
                 ".csv", ".log", ".yaml", ".yml", ".xml", ".ini", ".toml", ".sql"}

# 验收检查回读的文件内容/命令输出会原样进 checks[].detail 与 reason，最终落库并
# 展示在界面上——若文件里恰好有密钥（.env、测试夹带、生成的配置），会跟着外泄。
# 这里对对外字段做一层凭据扫描，命中即替换为 [REDACTED]。
_CRED_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),                                   # AWS Access Key ID
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),                 # PEM 私钥头
    re.compile(r"(?i)(token|passwd|password|secret|api[_-]?key)"
               r"(\s*[:=]\s*)([^\s]{6,})"),                            # key=value 形态
)


def _redact_secrets(text: Any) -> str:
    """把疑似凭据替换为 [REDACTED]；只影响对外展示/落库字段，不改源文件。"""
    s = str(text or "")
    for pat in _CRED_PATTERNS:
        if pat.groups >= 3:
            s = pat.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", s)
        else:
            s = pat.sub("[REDACTED]", s)
    return s
# shell 元字符：不经 shell 执行后这些只剩“裸词”形态，出现即说明命令依赖 shell 特性
_SHELL_TOKENS = frozenset({"&", "&&", "|", "||", "<", ">", ">>", "<<", ";"})


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
                       "detail": f"exists={exists} bytes={size}", "path": rel})
    return checks


def _run_command(command: Any, root: Path, timeout: float = 60) -> dict[str, Any]:
    """不经 shell 执行命令（shell=False）：字符串命令用 shlex 拆成参数列表。

    拆不开（引号不配对）或含 shell 特性（重定向/管道/&& 等，拆出来是裸元字符）
    时返回明确错误，不静默降级成 shell 执行——避免注入面。
    """
    if isinstance(command, str):
        if not command.strip():
            return {"ok": False, "detail": "empty command", "stdout": "", "stderr": ""}
        try:
            # Windows 用 posix=False 保留反斜杠路径，再逐 token 剥掉成对引号；
            # posix=True 会把未加引号的 C:\path 里的反斜杠当转义符吃掉。
            if os.name == "nt":
                argv = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"')
                        else t for t in shlex.split(command, posix=False)]
            else:
                argv = shlex.split(command, posix=True)
        except ValueError as exc:
            return {"ok": False,
                    "detail": f"命令无法拆分（引号不配对/Shell 语法）: {exc}",
                    "stdout": "", "stderr": ""}
        meta = [t for t in argv if t in _SHELL_TOKENS]
        if meta:
            return {"ok": False,
                    "detail": f"命令含 Shell 特性（{meta[0]}），不支持；请改写为直接命令",
                    "stdout": "", "stderr": ""}
    elif isinstance(command, (list, tuple)) and len(command) > 0:
        argv = [str(x) for x in command]
    else:
        return {"ok": False, "detail": "empty command", "stdout": "", "stderr": ""}
    try:
        proc = subprocess.run(argv, cwd=str(root), shell=False, capture_output=True,
                              text=True, timeout=max(1, min(float(timeout or 60), 300)))
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": "command timeout", "stdout": "", "stderr": ""}
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}",
                "stdout": "", "stderr": ""}
    return {"ok": True, "exit_code": proc.returncode,
            "stdout": (proc.stdout or "")[-8000:], "stderr": (proc.stderr or "")[-4000:]}


def _json_path(data: Any, path: str) -> Any:
    cur = data
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        else:
            return None
    return cur


def _format_ok(path: Path, fmt: str) -> tuple[bool, str]:
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        return False, str(exc)
    fmt = fmt.lower()
    if fmt == "json":
        try:
            json.loads(_read_text(path))
            return True, "valid json"
        except Exception as exc:
            return False, f"invalid json: {exc}"
    if fmt == "pdf":
        return head[:5] == b"%PDF-", "pdf header"
    if fmt == "png":
        return head[:8] == b"\x89PNG\r\n\x1a\n", "png header"
    if fmt == "zip":
        return head[:2] == b"PK", "zip header"
    if fmt == "nonempty":
        return path.stat().st_size > 0, f"bytes={path.stat().st_size}"
    return False, f"unsupported format {fmt}"


def _check_one(raw: Any, result: dict[str, Any], root: Path,
               allow_commands: bool) -> dict[str, Any]:
    # 验收条件是模型自填 JSON：float(raw.get("timeout"))/int(bytes)/int(exit_code)
    # 等脏转换抛 TypeError/ValueError 时在函数体 try 块外，会直接崩掉 run_plan。
    # 入口整体兜底：转换失败按"该条验收不通过"处理，不拖垮整轮验收。
    try:
        return _check_one_impl(raw, result, root, allow_commands)
    except (TypeError, ValueError) as exc:
        # 必须带 name：review_task 汇总失败原因时会读 c["name"]。
        return {"name": "bad_param", "ok": False, "detail": f"bad param: {exc}"}


def _check_one_impl(raw: Any, result: dict[str, Any], root: Path,
                    allow_commands: bool) -> dict[str, Any]:
    if isinstance(raw, str):
        return {"name": "manual", "ok": True, "manual": True,
                "detail": "manual: " + raw[:200]}
    if not isinstance(raw, dict):
        return {"name": "invalid", "ok": False, "detail": "condition must be object/string"}
    kind = str(raw.get("type") or "").strip().lower()
    timeout = float(raw.get("timeout") or 60)

    if kind == "file_exists":
        rel = str(raw.get("path") or "")
        target = _safe_path(root, rel)
        return {"name": "file_exists", "ok": bool(target and target.exists()),
                "detail": rel, "path": rel}
    if kind == "file_contains":
        rel = str(raw.get("path") or "")
        target = _safe_path(root, rel)
        text = _read_text(target) if target else ""
        needle = str(raw.get("text") or "")
        if raw.get("regex"):
            try:
                ok = bool(target and re.search(needle, text, re.M))
            except re.error:
                ok = False
        else:
            ok = bool(target and needle in text)
        return {"name": "file_contains", "ok": ok, "detail": rel, "path": rel}
    if kind == "file_min_bytes":
        rel = str(raw.get("path") or "")
        target = _safe_path(root, rel)
        minimum = int(raw.get("bytes") or 1)
        size = target.stat().st_size if target and target.is_file() else 0
        return {"name": "file_min_bytes", "ok": size >= minimum,
                "detail": f"{size} >= {minimum}", "path": rel}
    if kind == "file_sha256":
        rel = str(raw.get("path") or "")
        target = _safe_path(root, rel)
        expect = str(raw.get("sha256") or "").lower()
        actual = ""
        if target and target.is_file():
            h = hashlib.sha256(target.read_bytes()).hexdigest()
            actual = h
        return {"name": "file_sha256", "ok": bool(expect) and actual == expect,
                "detail": f"actual={actual}", "path": rel}
    if kind == "final_contains":
        needle = str(raw.get("text") or "")
        return {"name": "final_contains",
                "ok": bool(needle) and needle in str(result.get("content") or ""),
                "detail": f"needle={needle!r}"}
    if kind in ("tests_pass", "syntax_pass"):
        verification = result.get("verification") or {}
        key = "tests_passed" if kind == "tests_pass" else "syntax_passed"
        return {"name": kind, "ok": bool(verification.get(key)),
                "detail": f"child verification={verification}"}
    if kind == "format":
        rel = str(raw.get("path") or "")
        target = _safe_path(root, rel)
        ok, detail = (False, "missing file") if not target or not target.is_file() else (
            _format_ok(target, str(raw.get("format") or "")))
        return {"name": "format", "ok": ok, "detail": detail, "path": rel}
    if kind in ("command", "tests"):
        if not allow_commands:
            return {"name": kind, "ok": False,
                    "detail": "command checks disabled (allow_commands=false)"}
        if kind == "tests":
            # 参数列表直接传，不经 shell/引号拼接——路径含空格或引号时不会被拆开或注入
            path = str(raw.get("path") or "tests")
            pattern = str(raw.get("pattern") or "test_*.py")
            command = [sys.executable, "-m", "unittest", "discover",
                       "-s", path, "-p", pattern]
        else:
            command = str(raw.get("command") or "")
        run = _run_command(command, root, timeout)
        expected = int(raw.get("exit_code", 0))
        ok = bool(run.get("ok")) and int(run.get("exit_code", 1)) == expected
        stdout_needle = str(raw.get("stdout_contains") or "")
        if ok and stdout_needle:
            ok = stdout_needle in str(run.get("stdout") or "")
        return {"name": kind, "ok": ok,
                "detail": f"exit={run.get('exit_code')} {run.get('detail','')}".strip(),
                "stdout": str(run.get("stdout") or "")[-2000:],
                "stderr": str(run.get("stderr") or "")[-1000:]}
    if kind == "api":
        if not allow_commands:
            return {"name": "api", "ok": False,
                    "detail": "api checks disabled (allow_commands=false)"}
        url = str(raw.get("url") or "")
        if not url.startswith(("http://", "https://")):
            return {"name": "api", "ok": False, "detail": "only http(s) URLs"}
        try:
            import httpx
            method = str(raw.get("method") or "GET").upper()
            timeout_s = float(raw.get("timeout") or 20)
            with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
                resp = client.request(method, url, headers=raw.get("headers") or None,
                                      json=raw.get("json"))
            expected = int(raw.get("status") or 200)
            ok = resp.status_code == expected
            detail = f"status={resp.status_code} expected={expected}"
            if ok and raw.get("json_path"):
                try:
                    val = _json_path(resp.json(), str(raw["json_path"]))
                    expects = raw.get("equals", raw.get("contains"))
                    if "equals" in raw:
                        ok = val == raw["equals"]
                    elif "contains" in raw:
                        ok = str(raw["contains"]) in str(val)
                    detail += f" {raw['json_path']}={val!r}"
                except Exception as exc:
                    ok = False
                    detail += f" json error: {exc}"
            return {"name": "api", "ok": ok, "detail": detail}
        except Exception as exc:
            return {"name": "api", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    if kind == "all_of":
        subs = [_check_one(x, result, root, allow_commands) for x in raw.get("checks") or []]
        return {"name": "all_of", "ok": bool(subs) and all(x["ok"] for x in subs),
                "detail": f"{sum(x['ok'] for x in subs)}/{len(subs)} passed", "children": subs}
    if kind == "any_of":
        subs = [_check_one(x, result, root, allow_commands) for x in raw.get("checks") or []]
        return {"name": "any_of", "ok": any(x["ok"] for x in subs),
                "detail": f"{sum(x['ok'] for x in subs)}/{len(subs)} passed", "children": subs}
    if kind == "not":
        sub = _check_one(raw.get("check") or {}, result, root, allow_commands)
        return {"name": "not", "ok": not sub["ok"], "detail": "inverted",
                "children": [sub]}
    return {"name": kind or "unknown", "ok": True, "manual": True,
            "detail": f"unsupported check type: {kind}"}


def review_task(task: dict[str, Any], result: dict[str, Any], workspace: str | Path,
                *, allow_commands: bool = False) -> dict[str, Any]:
    """检查一次 Agent/子 Agent 结果；返回 {ok, verified, checks, reason}。"""
    root = Path(workspace).resolve()
    checks: list[dict[str, Any]] = []
    error = str(result.get("error") or "").strip()
    if error:
        error = _redact_secrets(error)
        return {"ok": False, "verified": False,
                "checks": [{"name": "agent_error", "ok": False, "detail": error}],
                "reason": error[:1000]}
    checks.extend(_check_outputs(task, root))
    for raw in task.get("acceptance") or []:
        checks.append(_check_one(raw, result, root, allow_commands))
    # 凭据脱敏：detail/reason 会落库并展示，文件内容/命令输出里的密钥不能原样外泄。
    for c in checks:
        if c.get("detail"):
            c["detail"] = _redact_secrets(c["detail"])
    failed = [c for c in checks if not c["ok"]]
    manual = [c for c in checks if c.get("manual")]
    verified = bool(checks) and not manual and not failed
    content = str(result.get("content") or "").strip()
    if not checks and not content:
        return {"ok": False, "verified": False, "checks": checks,
                "reason": "Agent 没有返回内容，也没有可检查的产物"}
    reason = ""
    if failed:
        reason = "; ".join(f"{c['name']}: {c.get('detail','')}" for c in failed)[:1500]
    elif not verified:
        reason = "没有可确定性验证的产物/验收条件；只能确认文本结果"
    high_risk = str(task.get("risk") or "").lower() in ("high", "critical")
    if high_risk and not verified:
        return {"ok": False, "verified": False, "checks": checks,
                "high_risk": True,
                "reason": reason or "高风险任务缺少确定性验证证据"}
    return {"ok": not failed, "verified": verified, "checks": checks,
            "failed": [c["name"] for c in failed], "manual": len(manual),
            "high_risk": high_risk, "reason": reason}


async def independent_review(task: dict[str, Any], result: dict[str, Any],
                             workspace: str | Path,
                             run_subagent: Callable[..., Awaitable[dict[str, Any]]],
                             *, model: str = "", allow_commands: bool = False) -> dict[str, Any]:
    """确定性检查 + 独立验证 Agent 的交叉检查。

    独立 Agent 只读产物；它不能推翻确定性检查失败，也不能替代文件/测试证据。
    """
    deterministic = review_task(task, result, workspace, allow_commands=allow_commands)
    if not deterministic.get("ok"):
        return {**deterministic, "independent": None}
    prompt = (
        "你是独立验证员。只读检查，不要修改文件。\n"
        f"任务：{task.get('title') or task.get('id')}\n"
        f"描述：{task.get('description') or ''}\n"
        f"产物：{json.dumps(task.get('outputs') or [], ensure_ascii=False)}\n"
        f"验收条件：{json.dumps(task.get('acceptance') or [], ensure_ascii=False)}\n"
        "请打开实际文件（read_text/list_directory/grep），核对产物是否真实、内容是否满足条件，"
        "不要相信子 Agent 的自述。最后只输出 JSON："
        '{"verdict":"pass|fail","issues":[{"title","evidence"}],"reason":"..."}'
    )
    try:
        ans = await run_subagent(prompt, "independent-verify", model, 30)
        text = str(ans.get("content") or "")
        m = re.search(r"\{.*\}", text, re.S)
        obj = json.loads(m.group(0)) if m else {}
    except Exception as exc:
        return {**deterministic, "independent": {"ok": False, "error": str(exc)},
                "ok": False, "reason": f"独立验证失败: {exc}"}
    if not isinstance(obj, dict):
        obj = {}
    verdict = str(obj.get("verdict") or "").lower()
    ok = verdict == "pass"
    return {**deterministic, "independent": {"ok": ok, "verdict": verdict,
                                             "issues": obj.get("issues") or [],
                                             "reason": str(obj.get("reason") or "")[:1000]},
            "ok": bool(deterministic.get("ok")) and ok,
            "verified": bool(deterministic.get("verified")) and ok,
            "reason": (deterministic.get("reason") or "") if ok else
                      ("独立验证未通过: " + str(obj.get("reason") or verdict))[:1500]}
