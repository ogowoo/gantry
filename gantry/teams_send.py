"""Teams 发送与"打开目标会话"：输入框、发送按钮、按消息片段/搜索定位会话。

依赖 `teams_dom`（页面与会话原语）与 `teams_common`（锁）；发送默认是显式调用，
插件侧还有 dry_run / send_enabled 两道闸。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as _html
import json
import re
import time
from pathlib import Path
from typing import Any

from . import bridge as _b
from .bridge import diagnostics
from .teams_common import (  # noqa: F401
    PAGE_LOCK_TIMEOUT, REPLY_TIMEOUT, TEAMS_URL, TeamsBusyError, TeamsError, _PAGE_LOCK,
    _norm_identity, _plain_text, acquire_page_lock, page_lock_stats, release_page_lock,
    verify_conversation_identity,
)

from .teams_dom import (  # noqa: F401
    _SEL, _first_visible, _goto_chat_home, chat_participants, header_title,
    open_chat, open_chat_by_row, teams_open, teams_page,
)

from .teams_api import api_send_message  # noqa: F401
async def compose_text(text: str, page=None, send: bool = False) -> dict[str, Any]:
    """在当前会话输入框里输入文本；send=True 时点发送按钮，否则只输入并清空。

    发送必须显式 `send=True`（插件侧还有 dry_run/send_enabled 两道闸）。
    与后台轮询/手动回复共用同一把页面锁。
    """
    await acquire_page_lock()
    try:
        return await _compose_text_unlocked(text, page=page, send=send)
    finally:
        release_page_lock()

async def _compose_text_unlocked(text: str, page=None, send: bool = False) -> dict[str, Any]:
    body = (text or "").strip()
    page = page or await teams_open()
    out: dict[str, Any] = {"len": len(body), "sent": False}
    if not body:
        return {**out, "error": "内容为空"}
    box = None
    for sel in _SEL["composer"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                box = loc
                break
        except Exception:                        # noqa: BLE001
            continue
    if box is None:
        return {**out, "error": "找不到输入框"}
    try:
        await box.click()
        # 多行必须用 Shift+Enter：`keyboard.type` 遇到 \n 会按 Enter 直接发送，
        # 一段回答会被拆成好几条 Teams 消息（用户实测："回复有点奇怪"）。
        lines = body.split("\n")
        for i, line in enumerate(lines):
            if i:
                await page.keyboard.press("Shift+Enter")
            await page.keyboard.type(line, delay=8)
        await asyncio.sleep(0.6)
    except Exception as exc:                     # noqa: BLE001
        return {**out, "error": f"输入失败: {str(exc)[:120]}"}
    # 发送按钮是否可用
    btn = None
    for sel in _SEL["send"]:
        try:
            cand = page.locator(sel).first
            if await cand.count() and await cand.is_visible():
                btn = cand
                break
        except Exception:                        # noqa: BLE001
            continue
    out["sendButton"] = bool(btn)
    if send and btn is not None:
        try:
            await btn.click()
            await asyncio.sleep(1.2)
            # 校验：输入框空了才说明 Teams 真的收下了这条消息。
            # （以前点完就当成功，实测有过"点了没反应但返回 sent=true"）
            left = ""
            try:
                left = (await box.inner_text() or "").strip()
            except Exception:                    # noqa: BLE001
                left = ""
            if not left:
                out["sent"] = True
                return out
            await page.keyboard.press("Enter")   # 兜底：Enter 再试一次
            await asyncio.sleep(1.2)
            try:
                left = (await box.inner_text() or "").strip()
            except Exception:                    # noqa: BLE001
                left = ""
            if not left:
                out["sent"] = True
                out["via"] = "enter"
                return out
            out["error"] = "点了发送但输入框还有内容（可能未发出）"
        except Exception as exc:                 # noqa: BLE001
            out["error"] = f"点发送失败: {str(exc)[:120]}"
    # 不发送：清空输入框（避免留下草稿）
    try:
        await box.click()
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        out["cleared"] = True
    except Exception:                            # noqa: BLE001
        pass
    return out

async def send_message(text: str) -> None:
    """在当前会话的输入框里发一条消息（Enter 发送）。"""
    await acquire_page_lock()
    try:
        await _send_message_unlocked(text)
    finally:
        release_page_lock()

async def _send_message_unlocked(text: str) -> None:
    body = (text or "").strip()
    if not body:
        raise TeamsError("消息内容为空")
    page = await teams_open()
    box = await _first_visible(page, "composer")
    if box is None:
        await diagnostics("teams_composer_missing")
        raise TeamsError("找不到输入框（Teams 版本可能改了 DOM：先跑 teams_probe 校准）")
    await box.click()
    # 多行内容：用 Shift+Enter 换行更稳（有些版本 Enter 直接发送）
    lines = body.split("\n")
    for i, line in enumerate(lines):
        if i:
            await page.keyboard.press("Shift+Enter")
        await page.keyboard.type(line, delay=0)
    await asyncio.sleep(0.3)
    # 优先点发送按钮（实测 [data-tid="sendMessageCommands-send"]；Enter 在部分版本会被
    # 输入法/快捷键吃掉），点不到再退回 Enter
    btn = None
    for sel in _SEL["send"]:
        try:
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible():
                btn = loc
                break
        except Exception:                       # noqa: BLE001
            continue
    if btn is not None:
        try:
            await btn.click()
        except Exception:                       # noqa: BLE001
            await page.keyboard.press("Enter")
    else:
        await page.keyboard.press("Enter")
    await asyncio.sleep(1.2)

async def _leave_chat(page) -> None:
    """发完回到聊天列表。

    会话一直开着时，新消息一到就会被 Teams 自动标成已读；`only_unread`
    会把它当成历史消息跳过（实测：用户 @ 了账号，但机器人刚在这个会话里回过，
    下一条消息 unread=False）。回到列表后新消息才会保持未读。
    """
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        # Teams 网页内置快捷键：Ctrl+2 = 聊天列表
        await page.keyboard.press("Control+2")
        await asyncio.sleep(1.2)
    except Exception:                           # noqa: BLE001
        pass

def _base_identity(text: str) -> str:
    """去掉 "姓名 - 公司/部门" 后缀，只留姓名部分。

    Teams 的 IndexedDB 里 peerName 常是 "Lynn Ling - Example"，但聊天头部可能只显示
    "Lynn Ling"；严格全名比较会把正确会话判成不一致（实测"定位不到窗口"）。
    """
    n = _norm_identity(text)
    return re.split(r"\s+[-–—]\s+", n, 1)[0].strip()


def _title_matches(head: str, expected: dict[str, Any] | None, peer: str = "") -> tuple[bool, str]:
    """校验打开的会话标题是否匹配期望的 topic/peer。"""
    exp = dict(expected or {})
    h = _norm_identity(head)
    topic = _norm_identity(exp.get("topic") or "")
    who = _norm_identity(exp.get("peer") or peer or "")
    if not h and (topic or who):
        return False, "会话标题为空，无法核验目标会话"
    if topic:
        if topic not in h and h not in topic:
            return False, f"标题不一致：期望「{exp.get('topic')}」，实际「{head}」"
    if who:
        # 全名优先；再退回"姓名部分"匹配（去掉 - 公司 后缀），避免正确会话被误拒。
        base = _base_identity(who)
        if who not in h and base not in h:
            return False, f"对端不一致：期望「{exp.get('peer') or peer}」，实际「{head}」"
    return True, ""

async def reply_to(peer: str, text: str, send: bool = False,
                   snippet: str = "", conv_id: str = "", *,
                   expected: dict[str, Any] | None = None,
                   strict: bool = False) -> dict[str, Any]:
    """打开 peer 的会话并准备回复（send=False 只输入并清空，绝不发送）。

    `snippet` = 该会话最后一条消息的片段：优先用它搜索（命中的是聊天），
    没有再用人名搜（人名可能命中联系人名片）。
    `conv_id` = IndexedDB 里的会话 id：send=True 时优先直接走 chatsvc API。
    `expected` = 发送前核验上下文（conv_id/peer/peer_guid/topic/snippet），
    任一不一致都会停止发送；`strict=True` 时 API 不可用也不回退 UI。
    所有 UI 操作与后台轮询共用同一把页面锁。
    """
    await acquire_page_lock()
    try:
        try:
            return await asyncio.wait_for(
                _reply_to_unlocked(peer, text, send=send, snippet=snippet,
                                   conv_id=conv_id, expected=expected,
                                   strict=strict),
                timeout=REPLY_TIMEOUT)
        except asyncio.TimeoutError:
            _b.log(f"reply_to 超时（>{REPLY_TIMEOUT:.0f}s），已取消并释放页面锁")
            return {"opened": False, "sent": False, "reason": "timeout",
                    "error": f"发送超时（>{REPLY_TIMEOUT:.0f}s），已取消"}
    finally:
        release_page_lock()

async def _reply_to_unlocked(peer: str, text: str, send: bool = False,
                             snippet: str = "", conv_id: str = "",
                             expected: dict[str, Any] | None = None,
                             strict: bool = False) -> dict[str, Any]:
    # 当前已经停在目标会话时走最快路径：不再抓 token/搜索/按行打开。
    # 这能避开 Teams 页面偶发卡死导致的 120s 超时。
    page = None
    if teams_page is not None and not teams_page.is_closed():
        try:
            page = teams_page
        except Exception:                        # noqa: BLE001
            page = None
    if page is None or "teams." not in (page.url or ""):
        page = await teams_open()
    if send and expected:
        try:
            head = await asyncio.wait_for(header_title(page), timeout=2)
        except Exception:                        # noqa: BLE001
            head = ""
        if head and _title_matches(head, expected, peer)[0]:
            res = await _compose_text_unlocked(text, page=page, send=send)
            if send and res.get("sent"):
                await _leave_chat(page)
            return {"opened": True, "how": "current", "header": head[:50], **res}
    api_res: dict[str, Any] | None = None
    if send and conv_id:
        try:
            api_res = await api_send_message(page, conv_id, text, peer=peer,
                                             snippet=snippet, expected=expected,
                                             lock=False)
            if api_res.get("ok"):
                return {"opened": True, "how": "api", "sent": True,
                        "id": api_res.get("id"), "status": api_res.get("status")}
            if api_res.get("reason") == "identity_mismatch":
                return {"opened": False, "sent": False,
                        "reason": "identity_mismatch",
                        "error": api_res.get("error") or "发送前身份校验失败",
                        "verification": api_res.get("verification")}
            not_found = (api_res.get("resolved") or {}).get("how") == "not-found"
            unavailable = api_res.get("reason") == "api_unavailable"
            if strict or conv_id:
                # 有些 1:1（例如自己的另一个账号/旧会话）不在 chatsvc 列表里，
                # 或暂时没有 skypetoken；此时允许回退 UI，但必须用标题/对端核验。
                # 真正的身份不一致（identity_mismatch）上面已经直接拒绝。
                expected_known = bool((expected or {}).get("peer") or
                                      (expected or {}).get("topic"))
                if (not_found or unavailable) and expected_known:
                    _b.log("API 不可用/找不到会话，回退 UI（仍校验标题/对端）")
                else:
                    return {"opened": False, "sent": False,
                            "reason": "strict_no_ui_fallback",
                            "error": api_res.get("error") or
                                     "API 发送失败，严格模式不退回 UI"}
            _b.log(f"reply_to API 发送失败（回退 UI）：{api_res.get('status')} {api_res.get('body')}")
        except Exception as exc:                 # noqa: BLE001
            api_res = {"ok": False, "error": str(exc)[:300]}
            if strict or conv_id:
                return {"opened": False, "sent": False,
                        "reason": "strict_no_ui_fallback",
                        "error": f"API 发送异常，严格模式不退回 UI：{str(exc)[:160]}"}
            _b.log(f"reply_to API 发送异常（回退 UI）：{str(exc)[:160]}")
    opened = False
    how = ""
    # 有会话 id 时优先用官方深链直接开会话：跳过搜索/联系人定位，
    # 避免"片段/人名定位不到窗口"（实测 Teams 搜索对 @ 前缀和同名联系人很敏感）。
    if not opened and conv_id:
        try:
            dl = await open_chat_by_deeplink(conv_id, page=page,
                                             expected=expected, peer=peer)
            if dl.get("opened"):
                opened = True
                how = "deeplink"
            else:
                _b.log(f"reply_to 深链未命中：{dl.get('error')}")
        except Exception as exc:                 # noqa: BLE001
            _b.log(f"reply_to 深链打开失败：{str(exc)[:120]}")
    # 第一优先：记忆里的会话在 @ 后会排到列表顶部，直接点行，避免搜索偏页
    if not opened and peer:
        try:
            if await open_chat_by_row(peer, snippet):
                opened = True
                how = "row"
        except Exception as exc:                 # noqa: BLE001
            _b.log(f"reply_to 按列表行打开失败：{str(exc)[:120]}")
    if not opened and snippet:
        res = await api_open_by_snippet(snippet, expect=peer, page=page)
        opened = bool(res.get("opened"))
        how = "snippet"
        if not opened:
            _b.log(f"reply_to 按消息片段打开失败：{res.get('error')} / {res.get('after')}")
    if not opened:
        # 搜索失败可能停在 "Search" 结果页；先回聊天列表再按人名点
        await _goto_chat_home(page)
        opened = await open_chat(peer)
        how = how or "name"
    if not opened:
        return {"opened": False, "error": f"打不开会话: {peer}"}
    head = await header_title(page)
    # 最后一道校验：打开的会话标题必须包含 peer/topic。否则宁可报错，也不能把回复
    # 发进别的群（实测同一个消息片段会命中多个会话，曾打开成 "AI GROUP Chat"）。
    ok, why = _title_matches(head, expected, peer)
    if not ok:
        return {"opened": False, "how": how, "header": (head or "")[:50],
                "reason": "identity_mismatch", "error": why}
    res = await _compose_text_unlocked(text, page=page, send=send)
    if send and res.get("sent"):
        await _leave_chat(page)
    return {"opened": True, "how": how, "header": (head or "")[:50], **res,
            **({"api": api_res} if api_res is not None else {})}

async def api_open_by_snippet(snippet: str, expect: str = "", page=None,
                               wait: float = 5.0, confirm: float = 30.0) -> dict[str, Any]:
    """用消息片段搜索并点开"最近消息"结果（比按人名搜可靠：人名会开名片）。"""
    page = page or await teams_open()
    try:
        if "/search" in (page.url or "").lower() or \
                "search |" in (await page.title() or "").lower():
            await _goto_chat_home(page)
    except Exception:                           # noqa: BLE001
        pass
    before = await header_title(page)
    out: dict[str, Any] = {"snippet": (snippet or "")[:40], "before": (before or "")[:40]}
    text = (snippet or "").strip()
    if not text:
        return {**out, "error": "缺少片段"}
    inp = page.locator('input[data-tid="AUTOSUGGEST_INPUT"]').first
    if not await inp.count():
        return {**out, "error": "找不到搜索框"}
    try:
        await inp.click()
        await asyncio.sleep(0.4)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(text[:40], delay=90)
        await asyncio.sleep(wait)
    except Exception as exc:                     # noqa: BLE001
        return {**out, "error": f"输入失败: {str(exc)[:120]}"}
    rows = await page.evaluate(r"""() => {
      const pop = document.querySelector('[data-tid="ms-searchux-popup"]');
      if (!pop) return [];
      const out = [];
      for (const el of pop.querySelectorAll('div,a,li')) {
        const t = (el.innerText || '').replace(new RegExp('\s+', 'g'), ' ').trim();
        if (!t || t.length < 12) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 120 || r.height < 16) continue;
        out.push({text: t.slice(0, 120), x: Math.round(r.x + r.width * 0.5),
                  y: Math.round(r.y + r.height * 0.5)});
      }
      return out.slice(0, 25);
    }""")
    out["rows"] = len(rows)
    # 选"消息"行：优先带 expect（会话标题/人名）的那一行——同一段最后消息可能
    # 同时出现在群和私聊里，只按片段选第一行会点错群（实测打开成 "AI GROUP Chat"）。
    cands = [r_ for r_ in rows if text[:8] in r_["text"]]
    if expect:
        pref = expect.lower()[:8]
        cands.sort(key=lambda r_: 0 if pref in r_["text"].lower() else 1)
    pick = cands[0] if cands else None
    if pick is None:
        return {**out, "opened": False, "error": "建议列表里没有匹配的消息行"}
    out["picked"] = pick["text"][:80]
    try:
        await page.mouse.click(pick["x"], pick["y"])
        await asyncio.sleep(2.5)
    except Exception as exc:                     # noqa: BLE001
        return {**out, "opened": False, "error": f"点击失败: {str(exc)[:120]}"}
    deadline = time.monotonic() + max(8.0, confirm)
    while time.monotonic() < deadline:
        head = await header_title(page)
        if head and head != before:
            out["after"] = head[:50]
            matched = (not expect) or expect.lower()[:8] in head.lower()
            out["matched"] = matched
            out["opened"] = matched
            if not matched:
                out["error"] = f"打开的是「{head[:40]}」，不是「{expect}」"
            return out
        await asyncio.sleep(1.5)
    out["after"] = (await header_title(page) or "")[:50]
    out["opened"] = False
    return out


async def api_search_rows(name: str, page=None, wait: float = 5.0) -> dict[str, Any]:
    """输入名字后 dump 建议列表的行（文本/角色/是否含 @），不按回车。"""
    page = page or await teams_open()
    inp = page.locator('input[data-tid="AUTOSUGGEST_INPUT"]').first
    if not await inp.count():
        return {"error": "找不到搜索框"}
    try:
        await inp.click()
        await asyncio.sleep(0.4)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(name, delay=110)
        await asyncio.sleep(wait)
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"输入失败: {str(exc)[:120]}"}
    rows = await page.evaluate(r"""() => {
      const pop = document.querySelector('[data-tid="ms-searchux-popup"]');
      if (!pop) return [];
      const out = [];
      const cand = [...pop.querySelectorAll('[role="option"],[role="row"],[role="button"],li,a,div')];
      const seen = new Set();
      for (const el of cand) {
        const t = (el.innerText || '').replace(new RegExp('\s+', 'g'), ' ').trim();
        if (!t || t.length > 160) continue;
        if (seen.has(t)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 40 || r.height < 12) continue;
        seen.add(t);
        out.push({text: t.slice(0, 120), role: el.getAttribute('role') || '',
                  tag: el.tagName, tid: el.getAttribute('data-tid') || '',
                  x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)});
        if (out.length >= 20) break;
      }
      return out;
    }""")
    return {"target": name, "rows": rows}

async def api_search_probe(page=None) -> dict[str, Any]:
    """搜索相关 DOM 结构（只读）。"""
    page = page or await teams_open()
    return await page.evaluate(r"""() => {
      const info = (el) => el ? ({
        tag: el.tagName, tid: el.getAttribute('data-tid') || '',
        role: el.getAttribute('role') || '',
        aria: (el.getAttribute('aria-label') || '').slice(0, 60),
        placeholder: (el.getAttribute('placeholder') || '').slice(0, 40),
        visible: !!(el.offsetWidth || el.offsetHeight)
      }) : null;
      const q = (sel) => [...document.querySelectorAll(sel)].slice(0, 4).map(info);
      return {
        active: info(document.activeElement),
        inputs: q('input'),
        comboboxes: q('[role="combobox"]'),
        searchTids: q('[data-tid*="search" i], [data-tid*="AUTOSUGGEST" i]'),
        textboxes: q('[role="textbox"]')
      };
    }""")

async def api_open_by_search(name: str, page=None, wait: float = 3.0) -> dict[str, Any]:
    """键盘搜索打开会话；返回是否成功与当前头部标题。"""
    page = page or await teams_open()
    before = await header_title(page)
    out: dict[str, Any] = {"target": name, "before": before}
    try:
        await page.keyboard.press("Control+e")
        await asyncio.sleep(1.2)
        await page.keyboard.type(name, delay=60)
        await asyncio.sleep(wait)                 # 等搜索建议
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.6)
        await page.keyboard.press("Enter")
        await asyncio.sleep(2.5)
    except Exception as exc:                     # noqa: BLE001
        return {**out, "error": str(exc)[:160]}
    deadline = time.monotonic() + 25
    after = ""
    while time.monotonic() < deadline:
        after = await header_title(page)
        if after and after != before:
            break
        await asyncio.sleep(1.5)
    out["after"] = after
    out["opened"] = bool(after) and (name.lower()[:8] in after.lower() or after != before)
    out["matched"] = bool(after) and name.lower()[:8] in after.lower()
    out["participants"] = await chat_participants() if hasattr(page, "locator") else None
    return out

async def open_chat_by_deeplink(conv_id: str, page=None, *,
                                expected: dict[str, Any] | None = None,
                                peer: str = "", wait: float = 15.0) -> dict[str, Any]:
    """用 `19:...` 会话 id 的官方深链直接打开聊天窗口。

    官方格式：`https://teams.microsoft.com/l/chat/<chatId>/conversations`。
    这比按片段/人名搜索可靠：不依赖消息前缀、联系人名片或搜索结果排序。
    """
    page = page or await teams_open()
    cid = str(conv_id or "").strip()
    if not cid:
        return {"opened": False, "error": "缺少会话 id"}
    urls = [
        "https://teams.cloud.microsoft/l/chat/" + cid + "/conversations",
        "https://teams.microsoft.com/l/chat/" + cid + "/conversations",
    ]
    out: dict[str, Any] = {"conv": cid[:60], "tries": []}
    for url in urls:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:                 # noqa: BLE001
            out["tries"].append({"url": url[:90], "error": str(exc)[:120]})
            continue
        deadline = time.monotonic() + max(3.0, float(wait))
        head = ""
        while time.monotonic() < deadline:
            head = await header_title(page)
            if head:
                break
            await asyncio.sleep(1)
        ok, why = _title_matches(head, expected, peer)
        out["tries"].append({"url": url[:90], "head": head[:50],
                             "matched": ok, "why": why[:80]})
        if ok:
            return {"opened": True, "how": "deeplink", "head": head[:50], **out}
    out["opened"] = False
    out["error"] = "深链未打开匹配会话"
    return out


async def api_open_by_url(conv_id: str, page=None) -> dict[str, Any]:
    """依次尝试几种深链形式打开会话，报告头部标题是否变化。"""
    page = page or await teams_open()
    before = await header_title(page)
    out: dict[str, Any] = {"conv": conv_id[:60], "before": (before or "")[:40], "tries": []}
    if not conv_id:
        return {**out, "error": "缺少会话 id"}
    base = "https://teams.cloud.microsoft/v2/"
    forms = [
        base + "#/chat/" + conv_id,
        base + "#/conversations/" + conv_id,
        base + "?threadId=" + conv_id,
        "https://teams.cloud.microsoft/l/chat/" + conv_id + "/conversations",
    ]
    for url in forms:
        rec: dict[str, Any] = {"url": url[:90]}
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(4)
            head = await header_title(page)
            rec["head"] = (head or "")[:50]
            rec["changed"] = bool(head) and head != before
            rec["participants"] = await chat_participants()
        except Exception as exc:                 # noqa: BLE001
            rec["error"] = str(exc)[:100]
        out["tries"].append(rec)
        if rec.get("changed"):
            out["winner"] = rec
            break
    return out

async def api_item_attrs(page=None, idx: int = 0) -> dict[str, Any]:
    """dump 第 idx 个会话项的属性与子元素特征（找可用于定位的 id）。"""
    page = page or await teams_open()
    return await page.evaluate("""({idx}) => {
      const items = [...document.querySelectorAll('[data-tid="chat-pane-item"]')];
      const el = items[idx];
      if (!el) return {count: items.length, error: 'no such item'};
      const attrs = {};
      for (const a of el.attributes) attrs[a.name] = String(a.value).slice(0, 80);
      const kids = [...el.querySelectorAll('*')].slice(0, 12).map(k => ({
        tag: k.tagName, tid: k.getAttribute('data-tid') || '',
        title: (k.getAttribute('title') || '').slice(0, 40),
        text: (k.innerText || '').slice(0, 40).replace(new RegExp('\\s+','g'),' ')
      }));
      return {count: items.length, attrs, kids,
              text: (el.innerText || '').slice(0, 120).replace(new RegExp('\\s+','g'),' ')};
    }""", {"idx": idx})

async def api_click_test(idx: int = 0, page=None, target: str = "") -> dict[str, Any]:
    """对第 idx 个会话项依次尝试多种点击方式，报告哪种能切换会话。"""
    page = page or await teams_open()
    before = await header_title(page)
    out: dict[str, Any] = {"before": (before or "")[:40], "tries": []}
    loc = page.locator('[data-tid="chat-pane-item"]').nth(int(idx))
    if not await loc.count():
        return {**out, "error": "找不到会话项"}
    try:
        await loc.scroll_into_view_if_needed(timeout=5000)
    except Exception:                            # noqa: BLE001
        pass
    box = await loc.bounding_box() or {}

    async def _changed() -> tuple[bool, str]:
        for _ in range(10):
            await asyncio.sleep(1.2)
            head = await header_title(page)
            if head and head != before:
                return True, head[:40]
        return False, (await header_title(page) or "")[:40]

    attempts: list[tuple[str, Any]] = []
    if box:
        cx, cy = box["x"] + box["width"] * 0.62, box["y"] + box["height"] * 0.5
        attempts.append(("mouse.click(62%)", lambda: page.mouse.click(cx, cy)))
        attempts.append(("mouse.down/up", lambda: _down_up(page, cx, cy)))
    attempts.append(("locator.click(force)", lambda: loc.click(
        force=True, timeout=8000, position={"x": 120, "y": 18})))
    for name, fn in attempts:
        rec: dict[str, Any] = {"how": name}
        try:
            await fn()
            ok, head = await _changed()
            rec["changed"] = ok
            rec["head"] = head
        except Exception as exc:                 # noqa: BLE001
            rec["error"] = str(exc)[:100]
        out["tries"].append(rec)
        if rec.get("changed"):
            out["winner"] = name
            break
    return out

async def _down_up(page, x: float, y: float) -> None:
    """按下/抬起分离（带小延迟），有些 React 列表只认这种序列。"""
    await page.mouse.move(x, y)
    await asyncio.sleep(0.2)
    await page.mouse.down()
    await asyncio.sleep(0.12)
    await page.mouse.up()

