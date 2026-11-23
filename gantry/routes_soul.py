# -*- coding: utf-8 -*-
"""Soul / Persona 的 API：读取、编辑、列举、历史与回滚。

- Soul 只有一份（安装目录 `SOUL.md`），改它等于改这个部署的"自我"，
  所以只允许这里读写；空字符串 = 删除文件、恢复内置默认。
- 每次改 Soul 前把旧文件快照到 `data/soul-history/<时间戳>/SOUL.md`，
  可列出、查看、回滚——身份是所有任务共享的，改动必须可追溯。
- Persona 是文件库：安装目录 `personas/*.md` + 工作区 `personas/*.md`
  （覆盖同名，可用 `agent.allow_workspace_personas` 关闭——工作区文件来自
  仓库，属于提示词注入面）。
- 保存时返回 `warnings`（soul.lint_text）：取消审批/验证、泄露密钥、
  覆盖安全规则等高危表述会被点名，但不阻止保存（用户可能确有特殊需求）。
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from . import bridge as b
from . import soul
from .tool_plugins._atomic import atomic_write_text

router = APIRouter()
HISTORY_KEEP = 50


def _roots() -> tuple[Path, Path | None]:
    """(安装目录, 工作区)。工作区可能还没绑定。"""
    g = getattr(b.AGENT_TOOLS, "global_root", None) or getattr(b, "BASE", None)
    ws = getattr(b.AGENT_TOOLS, "root", None)
    return (Path(g) if g else Path.cwd(),
            Path(ws) if ws else None)


def _history_dir() -> Path:
    return Path(getattr(b, "BASE", Path.cwd())) / "data" / "soul-history"


def _allow_workspace() -> bool:
    try:
        return bool((b.AGENT_CFG or {}).get("allow_workspace_personas", True))
    except Exception:
        return True


def _snapshot_soul(path: Path) -> str | None:
    """改动前快照当前 SOUL.md；返回快照目录名（没文件则不快照）。"""
    try:
        if not path.is_file():
            return None
    except Exception:
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    d = _history_dir() / stamp
    try:
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_text(d / "SOUL.md", path.read_text(encoding="utf-8", errors="replace"),
                          encoding="utf-8", newline="")
        _prune_history()
        return stamp
    except Exception as exc:
        b.log(f"SOUL 快照失败（继续保存）：{exc}")
        return None


def _prune_history() -> None:
    """只保留最近 HISTORY_KEEP 份（按目录名时间戳排序）。"""
    try:
        dirs = sorted([p for p in _history_dir().iterdir() if p.is_dir()])
        for old in dirs[:-HISTORY_KEEP]:
            for f in old.rglob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                old.rmdir()
            except Exception:
                pass
    except Exception:
        pass


@router.get("/v1/agent/soul")
async def get_soul():
    """读取当前 Soul（文件或内置默认）+ 结构化检查。"""
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
            "max_chars": soul.MAX_SOUL_CHARS,
            "chars": len(text or effective),
            "tokens": soul.estimate_tokens(text or effective),
            "warnings": soul.lint_text(text, "soul") if exists else [],
            "history_count": _history_count()}


def _history_count() -> int:
    try:
        return len([p for p in _history_dir().iterdir() if p.is_dir()])
    except Exception:
        return 0


@router.put("/v1/agent/soul")
async def put_soul(payload: dict | None = None):
    """保存 Soul。空文本 = 删除自定义文件、恢复内置默认。保存前自动快照。"""
    g, _ws = _roots()
    text = str((payload or {}).get("text") or "")
    path = g / soul.SOUL_FILE
    if not text.strip():
        snap = _snapshot_soul(path)
        try:
            if path.is_file():
                path.unlink()
        except Exception as exc:
            raise HTTPException(500, f"删除 SOUL.md 失败: {exc}") from exc
        return {"ok": True, "exists": False, "restored_default": True, "snapshot": snap}
    if len(text) > soul.MAX_SOUL_CHARS * 3:
        raise HTTPException(400, f"Soul 文本过长（上限 {soul.MAX_SOUL_CHARS * 3} 字符）")
    snap = _snapshot_soul(path)
    try:
        atomic_write_text(path, text, encoding="utf-8", newline="")
    except Exception as exc:
        raise HTTPException(500, f"写入 SOUL.md 失败: {exc}") from exc
    return {"ok": True, "exists": True, "path": str(path),
            "will_truncate": len(text) > soul.MAX_SOUL_CHARS,
            "warnings": soul.lint_text(text, "soul"),
            "snapshot": snap}


@router.get("/v1/agent/soul/history")
async def soul_history():
    """Soul 修改历史（新→旧）：每项含 id、时间、字符数与预览。"""
    out = []
    try:
        for d in sorted([p for p in _history_dir().iterdir() if p.is_dir()], reverse=True):
            f = d / "SOUL.md"
            try:
                txt = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
            except Exception:
                txt = ""
            out.append({"id": d.name, "chars": len(txt),
                        "preview": txt.strip().splitlines()[0][:80] if txt.strip() else "",
                        "mtime": d.stat().st_mtime if d.exists() else 0})
    except Exception:
        pass
    return {"ok": True, "items": out[:HISTORY_KEEP]}


@router.get("/v1/agent/soul/history/{hid}")
async def soul_history_item(hid: str):
    """查看某份历史快照原文。"""
    if not hid or "/" in hid or "\\" in hid or ".." in hid:
        raise HTTPException(400, "历史 id 不合法")
    p = _history_dir() / hid / "SOUL.md"
    try:
        if not p.is_file():
            raise HTTPException(404, "没有这份历史")
        return {"ok": True, "id": hid, "text": p.read_text(encoding="utf-8", errors="replace")}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"读取历史失败: {exc}") from exc


@router.post("/v1/agent/soul/restore")
async def soul_restore(payload: dict | None = None):
    """回滚到某份历史快照（回滚前先把当前版本也快照一份，可再撤销）。"""
    hid = str((payload or {}).get("id") or "").strip()
    if not hid or "/" in hid or "\\" in hid or ".." in hid:
        raise HTTPException(400, "历史 id 不合法")
    src = _history_dir() / hid / "SOUL.md"
    try:
        if not src.is_file():
            raise HTTPException(404, "没有这份历史")
        text = src.read_text(encoding="utf-8", errors="replace")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"读取历史失败: {exc}") from exc
    g, _ws = _roots()
    path = g / soul.SOUL_FILE
    snap = _snapshot_soul(path)
    try:
        atomic_write_text(path, text, encoding="utf-8", newline="")
    except Exception as exc:
        raise HTTPException(500, f"回滚写入失败: {exc}") from exc
    return {"ok": True, "restored": hid, "snapshot": snap,
            "warnings": soul.lint_text(text, "soul")}


@router.get("/v1/agent/personas")
async def list_personas():
    """列出可用 persona（内置 + 全局 + 工作区），带来源、字段与当前选中。"""
    g, ws = _roots()
    allow_ws = _allow_workspace()
    items = []
    for name in soul.list_personas(g, ws, allow_ws):
        meta = soul.persona_meta(name, g, ws, "zh", allow_ws)
        items.append({"name": name, "source": meta["source"],
                      "tokens": meta["tokens"], "fields": meta["fields"],
                      "extends": meta["extends"]})
    selected = soul.normalize_name((b.AGENT_CFG or {}).get("persona")) or soul.DEFAULT_PERSONA
    return {"ok": True, "items": items, "selected": selected,
            "allow_workspace": allow_ws,
            "workspace": str(ws) if ws else None}


@router.get("/v1/agent/personas/{name}")
async def get_persona(name: str):
    """读某个 persona：原文（文件）或内置文本，附生效元信息与告警。"""
    g, ws = _roots()
    n = soul.normalize_name(name)
    if not n:
        raise HTTPException(400, "persona 名不合法")
    allow_ws = _allow_workspace()
    meta = soul.persona_meta(n, g, ws, "zh", allow_ws)
    src = meta["source"]
    text = ""
    if src in ("workspace", "global"):
        d = (ws / soul.PERSONAS_DIR) if (ws and src == "workspace") else (g / soul.PERSONAS_DIR)
        try:
            text = (d / f"{n}.md").read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise HTTPException(500, f"读取 persona 失败: {exc}") from exc
    elif src == "builtin":
        builtin = soul.DEFAULT_PERSONAS.get(n)
        if builtin is None:
            raise HTTPException(404, f"没有这个 persona: {n}")
        text = "# " + n + "\n\n## zh\n" + builtin["zh"] + "\n\n## en\n" + builtin["en"]
    return {"ok": True, "name": meta["name"], "source": src, "text": text,
            "effective_text": meta["text"], "fields": meta["fields"],
            "tokens": meta["tokens"], "extends": meta["extends"],
            "max_chars": soul.MAX_PERSONA_CHARS,
            "warnings": soul.lint_text(text, f"persona {n}"),
            "workspace_warning": (
                "该 persona 来自当前工作区（仓库里的文件会进入系统提示词，属提示词注入面）"
                if src == "workspace" else "")}


@router.put("/v1/agent/personas/{name}")
async def put_persona(name: str, payload: dict | None = None):
    """写 persona 文件。scope=workspace（默认）/global；空文本 = 删除该文件。"""
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
    fields, _body = soul.parse_frontmatter(text)
    return {"ok": True, "path": str(path), "scope": scope,
            "will_truncate": len(text) > soul.MAX_PERSONA_CHARS,
            "fields": fields,
            "tokens": soul.estimate_tokens(soul.persona_text(n, g, ws, "zh", True)[1]),
            "warnings": soul.lint_text(text, f"persona {n}")}
