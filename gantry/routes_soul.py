# -*- coding: utf-8 -*-
"""Soul / Persona 的 API：读取、编辑、列举。

- Soul 只有一份（安装目录 `SOUL.md`），改它等于改这个部署的"自我"，
  所以只允许这里读写；内容为空字符串时删除文件 = 恢复内置默认。
- Persona 是文件库：安装目录 `personas/*.md`（内置库）+ 工作区
  `personas/*.md`（覆盖同名）。写工作区不影响其他项目，写全局则所有
  工作区可见——由调用方显式给 scope。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import bridge as b
from . import soul
from .tool_plugins._atomic import atomic_write_text

router = APIRouter()


def _roots() -> tuple[Path, Path | None]:
    """(安装目录, 工作区)。工作区可能还没绑定。"""
    g = getattr(b.AGENT_TOOLS, "global_root", None) or getattr(b, "BASE", None)
    ws = getattr(b.AGENT_TOOLS, "root", None)
    return (Path(g) if g else Path.cwd(),
            Path(ws) if ws else None)


def _persona_source(name: str) -> str:
    """该名字当前来自哪里：workspace / global / builtin。"""
    g, ws = _roots()
    for scope, d in (("workspace", (ws / soul.PERSONAS_DIR) if ws else None),
                     ("global", g / soul.PERSONAS_DIR)):
        if d is not None:
            try:
                if (d / f"{name}.md").is_file():
                    return scope
            except Exception:
                continue
    return "builtin"


@router.get("/v1/agent/soul")
async def get_soul():
    """读取当前 Soul（文件或内置默认）。"""
    g, _ws = _roots()
    path = g / soul.SOUL_FILE
    exists = False
    try:
        exists = path.is_file()
    except Exception:
        pass
    text = ""
    if exists:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(500, f"读取 SOUL.md 失败: {exc}") from exc
    _t, effective = soul.soul_text(g, "zh")
    return {"ok": True, "path": str(path), "exists": exists, "text": text,
            "effective_text": effective,
            "default_text": soul.DEFAULT_SOUL["zh"],
            "max_chars": soul.MAX_SOUL_CHARS}


@router.put("/v1/agent/soul")
async def put_soul(payload: dict | None = None):
    """保存 Soul。空文本 = 删除自定义文件、恢复内置默认。"""
    g, _ws = _roots()
    text = str((payload or {}).get("text") or "")
    path = g / soul.SOUL_FILE
    if not text.strip():
        try:
            if path.is_file():
                path.unlink()
        except Exception as exc:
            raise HTTPException(500, f"删除 SOUL.md 失败: {exc}") from exc
        return {"ok": True, "exists": False, "restored_default": True}
    if len(text) > soul.MAX_SOUL_CHARS * 3:
        raise HTTPException(400, f"Soul 文本过长（上限 {soul.MAX_SOUL_CHARS * 3} 字符）")
    try:
        atomic_write_text(path, text, encoding="utf-8", newline="")
    except Exception as exc:
        raise HTTPException(500, f"写入 SOUL.md 失败: {exc}") from exc
    return {"ok": True, "exists": True, "path": str(path),
            "will_truncate": len(text) > soul.MAX_SOUL_CHARS}


@router.get("/v1/agent/personas")
async def list_personas():
    """列出可用 persona（内置 + 全局 + 工作区），带来源与默认选中项。"""
    g, ws = _roots()
    items = []
    for name in soul.list_personas(g, ws):
        src = _persona_source(name)
        items.append({"name": name, "source": src})
    selected = soul.normalize_name((b.AGENT_CFG or {}).get("persona")) or soul.DEFAULT_PERSONA
    return {"ok": True, "items": items, "selected": selected,
            "workspace": str(ws) if ws else None}


@router.get("/v1/agent/personas/{name}")
async def get_persona(name: str):
    """读某个 persona 的原文（文件）或内置文本。"""
    g, ws = _roots()
    n = soul.normalize_name(name)
    if not n:
        raise HTTPException(400, "persona 名不合法")
    src = _persona_source(n)
    text = ""
    if src != "builtin":
        d = ((ws / soul.PERSONAS_DIR) if (ws and src == "workspace")
             else (g / soul.PERSONAS_DIR))
        try:
            text = (d / f"{n}.md").read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(500, f"读取 persona 失败: {exc}") from exc
    else:
        builtin = soul.DEFAULT_PERSONAS.get(n)
        if builtin is None:
            raise HTTPException(404, f"没有这个 persona: {n}")
        text = "# " + n + "\n\n## zh\n" + builtin["zh"] + "\n\n## en\n" + builtin["en"]
    _used, effective = soul.persona_text(n, g, ws, "zh")
    return {"ok": True, "name": n, "source": src, "text": text,
            "effective_text": effective, "max_chars": soul.MAX_PERSONA_CHARS}


@router.put("/v1/agent/personas/{name}")
async def put_persona(name: str, payload: dict | None = None):
    """写 persona 文件。scope=workspace（默认）或 global；空文本 = 删除该文件。"""
    g, ws = _roots()
    n = soul.normalize_name(name)
    if not n:
        raise HTTPException(400, "persona 名不合法（字母/数字/_/-，≤40）")
    data = payload or {}
    scope = str(data.get("scope") or "workspace").strip().lower()
    if scope not in ("workspace", "global"):
        raise HTTPException(400, "scope 必须是 workspace 或 global")
    if scope == "workspace" and ws is None:
        raise HTTPException(400, "当前没有工作区，无法写工作区 persona")
    text = str(data.get("text") or "")
    d = (ws / soul.PERSONAS_DIR) if scope == "workspace" else (g / soul.PERSONAS_DIR)
    path = d / f"{n}.md"
    if not text.strip():
        try:
            if path.is_file():
                path.unlink()
        except Exception as exc:
            raise HTTPException(500, f"删除 persona 失败: {exc}") from exc
        return {"ok": True, "deleted": True, "path": str(path)}
    if len(text) > soul.MAX_PERSONA_CHARS * 3:
        raise HTTPException(400, f"persona 文本过长（上限 {soul.MAX_PERSONA_CHARS * 3} 字符）")
    try:
        atomic_write_text(path, text, encoding="utf-8", newline="")
    except Exception as exc:
        raise HTTPException(500, f"写入 persona 失败: {exc}") from exc
    return {"ok": True, "path": str(path), "scope": scope,
            "will_truncate": len(text) > soul.MAX_PERSONA_CHARS}
