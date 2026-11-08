"""Shared helpers for file tool plugins."""
from __future__ import annotations

import ast
import base64
import difflib
import hashlib
import re
from pathlib import Path
from typing import Any

from .. import editmatch
from ..tools import ToolError


def guard_printable(text: str) -> None:
    """解出来的内容若明显不是文本（控制字符过多），拒绝并给出可行动提示。

    只拦"控制字符异常多"这种硬信号，避免误伤中文/二进制类合法文本
    （中文是可见字符，不算控制字符）。
    """
    if not text:
        return
    ctrl = sum(1 for ch in text if ord(ch) < 32 and ch not in "\t\n\r")
    if ctrl / max(1, len(text)) > 0.02:
        raise ToolError(
            "content_b64 解码后不像文本（控制字符占 "
            f"{ctrl / len(text):.0%}）：可能把原文直接当 Base64 塞进来了。"
            "纯文本/代码文件请改用 write_text(path, content=原文)，"
            "不要把内容做 Base64。")


def hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def read_key(registry, path: str) -> str:
    try:
        return str(registry._path(path)).casefold()
    except Exception:
        return str(path).casefold()


def mark_read(registry, path: str) -> None:
    """记录本会话读过的文件：编辑现有文件前必须有这个记录。"""
    sid = str(getattr(registry, "current_session_id", "") or "")
    cache = getattr(registry, "_read_files", None)
    if cache is None:
        cache = registry._read_files = {}
    cache.setdefault(sid, set()).add(read_key(registry, path))


def require_read(registry, path: str) -> None:
    sid = str(getattr(registry, "current_session_id", "") or "")
    # 直接调用 ToolRegistry 的脚本/单测没有会话上下文：不做读后编辑约束。
    # 真实 Agent 回合会在 agentrun 里设置 current_session_id，才启用该守卫。
    if not sid:
        return
    seen = (getattr(registry, "_read_files", None) or {}).get(sid) or set()
    if read_key(registry, path) not in seen:
        raise ToolError(
            f"编辑前必须先读取文件: {path}。请先 read_text（可用 line_start/line_count 只看目标段），"
            "再用 apply_patch / replace_text / replace_lines 做局部修改；不要凭记忆整篇重写。")


def diff_payload(path: str, before: str, after: str) -> dict[str, Any]:
    """返回结构化 diff：让模型/用户看到改了哪几行，而不是“写了多少字符”。"""
    before_lines = str(before or "").splitlines()
    after_lines = str(after or "").splitlines()
    diff_lines = list(difflib.unified_diff(
        before_lines, after_lines,
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))
    changed = sum(1 for line in diff_lines
                  if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    hunks = sum(1 for line in diff_lines if line.startswith("@@"))
    text = "\n".join(diff_lines)
    if len(text) > 12000:
        text = text[:12000] + "\n...（diff 过长，已截断）"
    return {"changed": before != after, "changed_lines": changed,
            "hunks": hunks, "diff": text}


def check_plain_code_size(registry, path: str, content: str) -> None:
    """代码类文件不允许"一大段明文塞进单个 JSON 动作"。"""
    suffix = Path(path).suffix.lower()
    if suffix in registry.code_exts and len(content) > registry.plain_code_limit:
        raise ToolError(
            f"{path} 是代码类文件，单次明文写入上限 {registry.plain_code_limit} 字符"
            f"（本次 {len(content)}）。请改用 write_text_b64 / append_text_b64："
            "Base64 分块每块不超过 3000 字符，写第一块用 write_text_b64、后续用 "
            "append_text_b64 追加。文档类（.md/.txt/.log）才允许明文分块直接写。")


def decode_b64(content_b64: str) -> str:
    try:
        raw = base64.b64decode(content_b64, validate=True)
        text = raw.decode("utf-8")
        guard_printable(text)
        return text
    except ToolError:
        raise
    except Exception as exc:
        s = (content_b64 or "").strip()
        hint = ""
        if s and not re.fullmatch(r"[A-Za-z0-9+/=\s]*", s):
            hint = ("；检测到 content_b64 含非 Base64 字符——请把原文按 UTF-8 做 "
                    "Base64 编码后放进 content_b64（纯文本文件也可改用 write_text/append_text）")
        raise ToolError(f"Base64 UTF-8 内容无效: {exc}{hint}") from exc


def nearest_hint(text: str, old: str) -> str:
    """片段没匹配上时，把文件里最接近的原文摘给模型，便于一轮内纠正。"""
    first = next((ln.strip() for ln in (old or "").splitlines() if ln.strip()), "")
    if not first:
        return ""
    lines = text.replace("\r\n", "\n").splitlines()
    for i, ln in enumerate(lines):
        if first in ln.strip():
            lo, hi = max(0, i - 1), min(len(lines), i + 3)
            excerpt = "\n".join(lines[lo:hi])[:600]
            return (f"。文件第 {i + 1} 行附近实际内容是：\n{excerpt}\n"
                    "请照抄其中的精确片段（注意缩进与行尾）后重试")
    return ""


def python_symbol_table(text: str) -> list[dict[str, Any]]:
    """列出 Python 顶层/嵌套函数与类（含限定名与行区间）。"""
    tree = ast.parse(text)
    out: list[dict[str, Any]] = []

    def walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                starts = [int(getattr(child, "lineno", 0))]
                starts += [int(getattr(d, "lineno", 0))
                           for d in getattr(child, "decorator_list", []) if getattr(d, "lineno", 0)]
                out.append({"name": name,
                            "kind": type(child).__name__,
                            "start": min(starts),
                            "end": int(getattr(child, "end_lineno", 0) or 0)})
                walk(child, name + ".")

    walk(tree)
    return out
