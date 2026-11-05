"""Teams 自动化共享底座：bridge 句柄、页面互斥锁、身份归一化。

这些被 dom / idb / probe / api / send 多个子模块共用，单独成层是为了避免循环依赖：
`teams_common` **不 import** 任何其它 teams_* 子模块。
"""
from __future__ import annotations

import asyncio
import html as _html
import re
from typing import Any

from . import bridge as _b

TEAMS_URL = str((_b.CFG.get("teams_url") or "https://teams.cloud.microsoft/v2"))

class TeamsError(RuntimeError):
    pass

class TeamsBusyError(TeamsError):
    """Teams 页面正被另一条发送/轮询流程占用（统一互斥锁超时）。"""


_PAGE_LOCK = asyncio.Lock()
PAGE_LOCK_TIMEOUT = 45.0
REPLY_TIMEOUT = 120.0

async def acquire_page_lock(timeout: float | None = None) -> None:
    """拿页面锁；超时抛 TeamsBusyError（调用方转成"忙，稍后再试"）。"""
    limit = PAGE_LOCK_TIMEOUT if timeout is None else float(timeout)
    try:
        await asyncio.wait_for(_PAGE_LOCK.acquire(), timeout=max(0.1, limit))
    except asyncio.TimeoutError as exc:
        raise TeamsBusyError("Teams 页面正忙（另一条发送/轮询未结束），请稍后再试") from exc

def release_page_lock() -> None:
    try:
        if _PAGE_LOCK.locked():
            _PAGE_LOCK.release()
    except RuntimeError:
        pass

def page_lock_stats() -> dict[str, Any]:
    return {"locked": bool(_PAGE_LOCK.locked()), "timeout": PAGE_LOCK_TIMEOUT}

def _norm_identity(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()

def _plain_text(raw: str) -> str:
    return _norm_identity(_html.unescape(re.sub(r"<[^>]+>", " ", str(raw or ""))))

def verify_conversation_identity(conv: dict[str, Any] | None,
                                 expected: dict[str, Any] | None) -> dict[str, Any]:
    """发送前核验 conversation_id、标题、对端身份。

    `expected` 可包含：conv_id / peer / peer_guid / topic / snippet。
    任一已知字段不一致都返回 ok=False；调用方必须停止发送。
    """
    exp = dict(expected or {})
    if not exp:
        return {"ok": True, "errors": []}
    if conv is None:
        return {"ok": False, "errors": ["会话记录不存在"]}
    errors: list[str] = []
    got_id = str(conv.get("id") or "")
    want_id = str(exp.get("conv_id") or "")
    if want_id and got_id != want_id:
        errors.append(f"conversation_id 不一致：期望 {want_id[:32]}，实际 {got_id[:32]}")
    topic = _norm_identity(conv.get("topic") or "")
    want_topic = _norm_identity(exp.get("topic") or "")
    if want_topic and topic != want_topic:
        errors.append(f"会话标题不一致：期望「{exp.get('topic')}」，实际「{conv.get('topic')}」")
    want_peer = _norm_identity(exp.get("peer") or "")
    peer_guid = _norm_identity(exp.get("peer_guid") or "")
    if peer_guid:
        if peer_guid not in got_id.casefold():
            errors.append("对端 GUID 不在 conversation_id 里")
    elif want_peer:
        hay = " ".join([topic, _norm_identity(conv.get("lastFrom") or ""),
                        _norm_identity(conv.get("topic") or "")])
        if want_peer not in hay:
            errors.append(f"对端身份不一致：期望「{exp.get('peer')}」")
    snippet = _plain_text(exp.get("snippet") or "")
    if snippet:
        last = _plain_text(conv.get("last") or "")
        # 只比较前 20 个字符：消息可能被 HTML 截断，但开头足以定位
        if last and snippet[:20] not in last:
            errors.append("最后一条消息内容不一致（会话可能已切换）")
    return {"ok": not errors, "errors": errors, "conversation": conv}

