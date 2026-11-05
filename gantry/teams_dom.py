"""Teams 页面/会话的 DOM 自动化底座：开页面、切标签、等渲染、读头部与消息。

只依赖 `teams_common`（锁/身份）与 bridge；**不 import** send/api，保持单向依赖。
选择器全部走 `_SEL` 的候选列表：Teams 改版时先改这里，再用 `probe()` 核对。
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
    PAGE_LOCK_TIMEOUT, REPLY_TIMEOUT, TEAMS_URL, TeamsAuthRequired, TeamsBusyError,
    TeamsError, _PAGE_LOCK,
    _norm_identity, _plain_text, acquire_page_lock, page_lock_stats, release_page_lock,
    verify_conversation_identity,
)
def _msg_key(author: str, text: str, ordinal: int) -> str:
    return hashlib.sha1(f"{author}|{text}|{ordinal}".encode()).hexdigest()[:16]

async def _pane_fingerprint(page) -> str:
    """当前消息区"指纹"：首条 + 末条消息文本，用来判断会话是否真的切换了。"""
    try:
        rows = await recent_messages(2, my_name="", _settle_first=False)
    except TypeError:                            # 兼容旧签名（此处不加参数）
        rows = await recent_messages(2, my_name="")
    except Exception:                            # noqa: BLE001
        return ""
    if not rows:
        return ""
    first = (rows[0].get("text") or "")[:60]
    last = (rows[-1].get("text") or "")[:60]
    return f"{first}|{last}"

async def header_title(page) -> str:
    """当前会话头部标题（用于确认"真的切过去了"）。"""
    for sel in ('[data-tid="entity-header"]', '[data-tid="chat-title"]'):
        try:
            el = page.locator(sel).first
            if await el.count():
                txt = (await el.inner_text(timeout=1500)) or ""
                txt = re.sub(r"\s+", " ", txt).strip()
                if txt:
                    return txt[:80]
        except Exception:                       # noqa: BLE001
            continue
    return ""

async def _real_click(page, loc) -> bool:
    """用真实鼠标事件点元素中心（React 的 onClick 只认真实指针事件）。"""
    try:
        await loc.scroll_into_view_if_needed(timeout=5000)
    except Exception:                            # noqa: BLE001
        pass
    try:
        box = await loc.bounding_box()
    except Exception:                            # noqa: BLE001
        box = None
    if box and box.get("width") and box.get("height"):
        try:
            # ⚠ 不要点中心：会话项左边是头像，中心会命中"用户资料卡"（实测踩到）。
            # 取右侧 65% 宽处 = 名字/预览文字区。
            await page.mouse.click(box["x"] + box["width"] * 0.65,
                                   box["y"] + box["height"] * 0.5)
            return True
        except Exception as exc:                 # noqa: BLE001
            _b.log(f"mouse.click 失败：{str(exc)[:100]}")
    for attempt in ("force-click", "js-click"):
        try:
            if attempt == "force-click":
                await loc.click(timeout=6000, force=True)
            else:
                await loc.evaluate("el => el.click()")
            return True
        except Exception as exc:                 # noqa: BLE001
            _b.log(f"open_chat {attempt} 失败：{str(exc)[:100]}")
    return False

async def recent_messages(limit: int = 30, my_name: str = "",
                          _settle_first: bool = True) -> list[dict[str, Any]]:
    """读当前会话最近的消息。

    返回 [{ordinal, author, text, mine, key, time}]，按页面顺序（早 → 晚）。
    `mine` 判定：作者名等于 my_name，或气泡带 me/own 标记；判定不出来时按文本前缀兜底。
    """
    page = await teams_open()
    # 先等渲染稳定：Teams 是渐进渲染，点开就立刻读经常读到 0 条或读到一半
    if _settle_first:
        await _settle(page, "message", rounds=3, delay=0.9)
    rows: list[dict[str, Any]] = []
    me = await signed_in_name(page)
    seen_sel = set()
    for sel in _SEL["message"]:
        if sel in seen_sel:
            continue
        seen_sel.add(sel)
        try:
            loc = page.locator(sel)
            n = await loc.count()
        except Exception:                       # noqa: BLE001
            continue
        if not n:
            continue
        start = max(0, n - limit)
        for i in range(start, n):
            item = loc.nth(i)
            try:
                if not await item.is_visible():
                    continue
                # 作者：从气泡往上找"只包含本条消息"的祖先，再从里面取 author。
                # 走到含多条消息的祖先就停 —— 否则会抓到上一条消息的作者（这就是之前的 bug）。
                author = ""
                try:
                    author = (await item.evaluate("""(el) => {
                      const MPICK = '[data-tid="chat-pane-message"]';
                      const APICK = '[data-tid="message-author-name"], [data-tid="message-avatar"]';
                      const clean = (a) => ((a.innerText || '').trim()
                                            || (a.getAttribute('aria-label') || '').trim());
                      let node = el;
                      for (let i = 0; i < 8 && node; i++) {
                        if (node.querySelectorAll && node.querySelectorAll(MPICK).length > 1) break;
                        const a = node.querySelector ? node.querySelector(APICK) : null;
                        if (a) {
                          const t = clean(a);
                          if (t) return t;
                        }
                        node = node.parentElement;
                      }
                      return '';
                    }""")) or ""
                except Exception:                       # noqa: BLE001
                    author = ""
                if not author.strip():
                    for asel in _SEL["author"]:
                        try:
                            a = item.locator(asel).first
                            if not await a.count():
                                continue
                            author = (await a.inner_text(timeout=1000)) or ""
                            if not author.strip():
                                author = (await a.get_attribute("aria-label")) or ""
                            if author.strip():
                                break
                        except Exception:       # noqa: BLE001
                            continue
                text = ""
                for tsel in _SEL["text"]:
                    try:
                        t = item.locator(tsel).first
                        if await t.count():
                            text = (await t.inner_text(timeout=1000)) or ""
                            break
                    except Exception:           # noqa: BLE001
                        continue
                if not text:
                    text = (await item.inner_text(timeout=1000)) or ""
                # 只读 `data-message-type=Chat` 的正常聊天消息：系统事件/成员变更
                # 不该被当成回答上下文。
                mtype = (await item.get_attribute("data-message-type")) or ""
                if not mtype:
                    try:
                        mtype = await item.evaluate("""(el) => {
                          const n = el.closest('[data-message-type]');
                          return n ? (n.getAttribute('data-message-type') || '') : '';
                        }""") or ""
                    except Exception:           # noqa: BLE001
                        mtype = ""
                if mtype and mtype.strip().lower() not in (
                        "chat", "text", "richtext", "richtext/html"):
                    continue
                author = re.sub(r"\s+", " ", author).strip()
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if not text:
                    continue
                # "自己发的"：只认显式 my_name；Teams 页面拿不到登录名，
                # 所以判不出来就当"不是自己"（真正防自问自答靠 @ 触发词 + seen）。
                who = (my_name or "").strip().lower()
                mine = bool(who) and author.strip().lower() == who
                if not mine:
                    try:
                        tid = (await item.get_attribute("data-tid")) or ""
                        cls = (await item.get_attribute("class")) or ""
                        mine = bool(re.search(r"(message-me|own-message|-mine\b)", tid + " " + cls))
                    except Exception:           # noqa: BLE001
                        pass
                rows.append({"ordinal": i, "author": author, "text": text, "mine": mine,
                             "key": _msg_key(author, text, i), "time": ""})
            except Exception:                   # noqa: BLE001
                continue
        if rows:
            break
    if not rows:                            # 还空就再等一轮（慢机器/弱网）
        await asyncio.sleep(1.5)
        await _settle(page, "message", rounds=3, delay=0.9)
    # 连续消息只在第一条显示作者 → 用继承补齐，避免"后面几条作者为空"
    from .teams_logic import inherit_authors

    filled = inherit_authors([r.get("author") or "" for r in rows])
    for r, a in zip(rows, filled):
        r["author"] = a
        if not r.get("mine") and my_name:
            r["mine"] = a.strip().lower() == my_name.strip().lower()
    return rows

async def dom_summary(page) -> dict[str, Any]:
    """在页面里统计结构：data-tid 计数、role 计数、像"列表"的容器、iframe 列表。

    比 dump 整页 HTML 有用得多：Teams 的虚拟列表在 React 里，HTML 动辄几百 KB，
    一截断就什么都看不到；计数与容器尺寸反而能一眼定位会话列表在哪。
    """
    try:
        return await page.evaluate(r"""() => {
          const tids = {}, roles = {};
          document.querySelectorAll('[data-tid]').forEach(el => {
            const t = el.getAttribute('data-tid'); tids[t] = (tids[t] || 0) + 1;
          });
          document.querySelectorAll('[role]').forEach(el => {
            const r = el.getAttribute('role'); roles[r] = (roles[r] || 0) + 1;
          });
          const pick = '[role="treeitem"],[role="option"],[role="listitem"],[role="row"]';
          const listish = [];
          document.querySelectorAll('[role="tree"],[role="list"],[role="listbox"],[role="grid"],nav,ul')
            .forEach(el => {
              const n = el.querySelectorAll(pick).length;
              if (!n) return;
              const r = el.getBoundingClientRect();
              listish.push({tag: el.tagName, role: el.getAttribute('role') || '',
                            tid: el.getAttribute('data-tid') || '',
                            aria: (el.getAttribute('aria-label') || '').slice(0, 60),
                            items: n, w: Math.round(r.width), h: Math.round(r.height)});
            });
          // 会话项样本：第一个 treeitem/option 的文本与属性
          const first = document.querySelector(pick);
          const sample = first ? {
            tag: first.tagName, role: first.getAttribute('role') || '',
            tid: first.getAttribute('data-tid') || '',
            aria: (first.getAttribute('aria-label') || '').slice(0, 80),
            text: (first.innerText || '').slice(0, 120).replace(/\s+/g, ' ')
          } : null;
          const ed = document.querySelector('[contenteditable="true"], [role="textbox"]');
          const iframes = [...document.querySelectorAll('iframe')]
            .map(f => ({id: f.id || '', src: (f.src || '').slice(0, 80)}));
          return {tids, roles, listish: listish.slice(0, 12), sample, iframes,
                  editable: ed ? {tag: ed.tagName, tid: ed.getAttribute('data-tid') || '',
                                  aria: (ed.getAttribute('aria-label') || '').slice(0, 60)}
                               : null};
        }""")
    except Exception as exc:                     # noqa: BLE001
        return {"error": str(exc)[:200]}

async def _first_visible(page, key: str):
    """在候选选择器里找第一个**可见**元素（顺序从具体到宽松）。"""
    for sel in _SEL.get(key, []):
        try:
            loc = page.locator(sel)
            n = await loc.count()
        except Exception:                       # noqa: BLE001
            continue
        for i in range(n - 1, -1, -1):
            try:
                if await loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:                   # noqa: BLE001
                continue
    return None

async def _visible_count(page, key: str) -> int:
    total = 0
    seen_sel = set()
    for sel in _SEL.get(key, []):
        if sel in seen_sel:
            continue
        seen_sel.add(sel)
        try:
            loc = page.locator(sel)
            n = await loc.count()
        except Exception:                       # noqa: BLE001
            continue
        for i in range(n):
            try:
                if await loc.nth(i).is_visible():
                    total += 1
            except Exception:                   # noqa: BLE001
                pass
        if total:
            return total                        # 用第一个命中选择器的计数，避免重复累加
    return total

def _clean_me(raw: str) -> str:
    """把 me-control 的 aria-label 收拾成"人名字符串"。

    实测这类标签常带前缀（"Account manager for …" / "Settings and more"），
    直接和作者名比会永远不等。
    """
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    text = _ME_PREFIX.sub("", text)
    return text.strip(" ,，.。:：")

async def signed_in_name(page) -> str:
    """当前登录 Teams 的显示名（取不到返回空串）。"""
    for sel in ('[data-tid="me-control-avatar"]',
                '[data-tid="me-control-avatar-trigger"]',
                '[data-tid="me-control"]'):
        try:
            el = page.locator(sel).first
            if not await el.count():
                continue
            for getter in ("aria-label", "title"):
                raw = (await el.get_attribute(getter)) or ""
                name = _clean_me(raw)
                if name:
                    return name
            name = _clean_me((await el.inner_text(timeout=800)) or "")
            if name:
                return name
        except Exception:                       # noqa: BLE001
            continue
    return ""

async def _settle(page, key: str, rounds: int = 3, delay: float = 0.9) -> int:
    """等某个区域"渲染稳定"：连续两次元素数量一致才算稳（Teams 是渐进渲染的）。

    用户反馈："你检测太快了，Teams 的页面有时没有那么快出来"——
    以前点开会话立刻读，经常读到 0 条或读到一半。
    """
    last = -1
    for _ in range(max(2, rounds)):
        n = await _visible_count(page, key)
        if n == last:
            return n
        last = n
        await asyncio.sleep(delay)
    return max(0, last)

async def _warm_ready(page) -> bool:
    """聊天区是否真的可用了：出现会话项或输入框（不是只看 app shell）。"""
    for key in ("chat_item", "composer"):
        try:
            loc = page.locator(":is(" + "), :is(".join(
                f"{sel}" for sel in _SEL[key]) + ")")
            n = await loc.count()
            if n:
                for i in range(n):
                    try:
                        if await loc.nth(i).is_visible():
                            return True
                    except Exception:           # noqa: BLE001
                        continue
        except Exception:                       # noqa: BLE001
            continue
    return False

async def _goto_chat_home(page) -> None:
    """回到 Teams 聊天列表。

    搜索建议点偏了会停在 "Search | ..." 结果页；那里没有会话列表/输入框，
    `teams_open()` 会一直等不到就绪、最后 300s 超时（实测："没能完成发送"）。
    """
    try:
        await page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=120_000)
        await asyncio.sleep(2.0)
    except Exception:                           # noqa: BLE001
        pass

async def _existing_teams_page(ctx) -> Any:
    """在已打开的标签页里找 Teams，避免每次重启/重载都新开一个。"""
    pages = []
    try:
        pages = list(ctx.pages)
    except Exception:                            # noqa: BLE001
        return None
    for p in reversed(pages):                    # 后开的通常更接近当前
        try:
            if p.is_closed():
                continue
            url = (p.url or "").lower()
        except Exception:                        # noqa: BLE001
            continue
        if "teams.cloud.microsoft" in url or "teams.microsoft.com" in url:
            return p
    return None

async def teams_tabs() -> dict[str, Any]:
    """只读：当前浏览器里有哪些 Teams 标签页（含 Gantry 自己开的）。"""
    ctx = _b.ctx
    if ctx is None:
        return {"count": 0, "tabs": []}
    out = []
    try:
        pages = list(ctx.pages)
    except Exception:                            # noqa: BLE001
        pages = []
    for i, p in enumerate(pages):
        try:
            if p.is_closed():
                continue
            url = p.url or ""
            title = (await p.title()) or ""
        except Exception:                        # noqa: BLE001
            continue
        is_teams = ("teams.cloud.microsoft" in url.lower()
                    or "teams.microsoft.com" in url.lower())
        if not is_teams:
            continue
        out.append({"index": i, "url": url[:100], "title": title[:100],
                    "gantry": any(m in title for m in _GANTRY_TAB_MARKERS),
                    "current": p is teams_page})
    return {"count": len(out), "tabs": out}

async def close_extra_gantry_tabs() -> dict[str, Any]:
    """关掉 Gantry 自己开的多余 Teams 标签页，只保留当前/第一个。

    只处理标题带 `[GANTRY-AGENT]`/`[CBP-AGENT]` 的页面，不碰用户自己开的
    "Chat | ..." 标签，避免误关用户页面。
    """
    global teams_page
    ctx = _b.ctx
    if ctx is None:
        return {"closed": 0, "kept": "", "reason": "浏览器未启动"}
    owned = []
    try:
        pages = list(ctx.pages)
    except Exception:                            # noqa: BLE001
        pages = []
    for p in pages:
        try:
            if p.is_closed():
                continue
            url = (p.url or "").lower()
            title = (await p.title()) or ""
        except Exception:                        # noqa: BLE001
            continue
        if ("teams.cloud.microsoft" in url or "teams.microsoft.com" in url) \
                and any(m in title for m in _GANTRY_TAB_MARKERS):
            owned.append(p)
    if not owned:
        return {"closed": 0, "kept": "", "reason": "没有 Gantry 自开的 Teams 标签"}
    keep = teams_page if teams_page in owned else owned[0]
    closed = 0
    for p in owned:
        if p is keep:
            continue
        try:
            await p.close()
            closed += 1
        except Exception:                        # noqa: BLE001
            pass
    teams_page = keep
    return {"closed": closed, "kept": (keep.url or "")[:100],
            "title": ((await keep.title()) or "")[:100]}

async def teams_open(timeout: float = 300.0, force_reload: bool = False) -> Any:
    """打开/复用 Teams 标签页，等到主界面出现。SSO 已在共享 ctx 里，通常只需几秒。"""
    global teams_page
    # ctx 是懒启动的：先确保浏览器起来，否则 _b.ctx 还是 None。
    # 注意：不能等 Copilot 就绪——Copilot 区域被 blocked 时 Teams 也要能用。
    try:
        _raw = getattr(_b, "ENSURE_BROWSER_RAW", None)
        if callable(_raw):
            await _raw()
        else:
            await _b.ensure_browser_ready()
    except Exception as exc:                    # noqa: BLE001
        raise TeamsError(f"启动浏览器失败：{exc}") from exc
    ctx = _b.ctx
    if teams_page is None or teams_page.is_closed():
        # 服务重启/模块热重载后全局变量会丢；先复用现有 Teams 标签页，
        # 不要每次都 ctx.new_page()（用户实测会堆一排重复标签）。
        teams_page = await _existing_teams_page(ctx)
        if teams_page is None:
            teams_page = await ctx.new_page()
            force_reload = True
    if force_reload or "teams." not in (teams_page.url or ""):
        try:
            await teams_page.goto(TEAMS_URL, wait_until="domcontentloaded", timeout=120_000)
        except Exception as exc:                # noqa: BLE001
            raise TeamsError(f"打开 Teams 失败：{exc}") from exc
    deadline = time.monotonic() + timeout
    warming_logged = False
    while time.monotonic() < deadline:
        try:
            url = teams_page.url or ""
            if "unsupported-browser" in url:
                await diagnostics("teams_unsupported_browser")
                raise TeamsError("Teams 判定浏览器不受支持（unsupported-browser）——"
                                 "确认走的是 Playwright 的 Chromium/Edge，而不是纯 HTTP 客户端")
            if await _first_visible(teams_page, "login_wall"):
                await diagnostics("teams_login_wall")
                raise TeamsAuthRequired("Teams 要求登录（共享浏览器里没有有效的 Teams 会话）："
                                        "先在自动化浏览器里登录一次 Teams")
            # 搜索页不是聊天页：回列表，别在这里干等 300s
            try:
                _title = (await teams_page.title() or "").lower()
            except Exception:                   # noqa: BLE001
                _title = ""
            if "/search" in url.lower() or "search |" in _title:
                _b.log("Teams 停在搜索页，返回聊天列表")
                await _goto_chat_home(teams_page)
                continue
            # 真的就绪：会话项或输入框已经出现
            if await _warm_ready(teams_page):
                return teams_page
            # 首次运行/大版本更新后会停在"We're setting things up for you…"：
            # 这不是错误，只能等（实测几分钟），但要让人从日志看出来在等什么
            if not warming_logged:
                body = ""
                try:
                    body = (await teams_page.inner_text("body"))[:400]
                except Exception:               # noqa: BLE001
                    pass
                if "setting" in body.lower() or "设置" in body:
                    warming_logged = True
                    _b.log("Teams 正在首次初始化（We're setting things up…），等它完成")
        except TeamsError:
            raise
        except Exception:                       # noqa: BLE001
            pass
        await asyncio.sleep(2.5)
    await diagnostics("teams_not_ready")
    raise TeamsError(f"Teams 界面在 {int(timeout)}s 内没准备好"
                     f"（首次初始化/新手引导可能还没走完；可再调大 teams_open 的 timeout）")

async def chat_participants() -> int | None:
    """当前会话的参与人数（读头部标题栏；取不到返回 None）。

    用途：**只对 1:1 会话应答**。实测群聊头部有 `chat-header-participant-count`，
    1:1 会话显示 2（你 + 对方）。
    """
    page = await teams_open()
    sel = '[data-tid="chat-header-participant-count"]'
    try:
        el = page.locator(sel).first
        if await el.count():
            raw = (await el.inner_text(timeout=1500)) or \
                  (await el.get_attribute("aria-label")) or ""
            m = re.search(r"\d+", raw)
            if m:
                return int(m.group(0))
    except Exception:                           # noqa: BLE001
        pass
    # 兜底：头部 aria-label 里常写 "2 members" / "2 个成员"
    try:
        head = page.locator('[data-tid="entity-header"]').first
        if await head.count():
            raw = (await head.get_attribute("aria-label")) or ""
            m = re.search(r"(\d+)\s*(member|participant|成员|位)", raw, re.I)
            if m:
                return int(m.group(1))
    except Exception:                           # noqa: BLE001
        pass
    return None

async def chat_kind() -> str:
    """当前会话类型：direct / group / unknown（见 teams_logic.classify_chat）。"""
    from .teams_logic import classify_chat

    return classify_chat(await chat_participants())

async def list_chats(limit: int = 20) -> list[dict[str, Any]]:
    """列出左侧会话列表：[{index, title, preview, unread}]（title 尽量取，取不到给空串）。"""
    page = await teams_open()
    items = []
    seen_sel = set()
    for sel in _SEL["chat_item"]:
        if sel in seen_sel:
            continue
        seen_sel.add(sel)
        try:
            loc = page.locator(sel)
            n = await loc.count()
        except Exception:                       # noqa: BLE001
            continue
        for i in range(min(n, limit)):
            item = loc.nth(i)
            try:
                if not await item.is_visible():
                    continue
                label = (await item.get_attribute("aria-label")) or ""
                text = (await item.inner_text(timeout=2000)) or ""
                if not label:
                    for tsel in _SEL["chat_title"]:
                        try:
                            cand = item.locator(tsel).first
                            if await cand.count():
                                label = (await cand.get_attribute("title")) or ""
                                if label.strip():
                                    break
                        except Exception:           # noqa: BLE001
                            continue
            except Exception:                   # noqa: BLE001
                continue
            title = (label or text.splitlines()[0] if text else "").strip()
            items.append({
                "index": i,
                "title": re.sub(r"\s+", " ", title)[:80],
                "preview": re.sub(r"\s+", " ", text)[:160],
                "unread": bool(re.search(r"unread|未读", label + text, re.I)),
            })
        if items:
            break
    return items

async def open_chat_by_row(peer: str, snippet: str = "") -> bool:
    """按聊天列表行直接打开会话（优先于点搜索/深链）。

    收到 @ 的会话会立刻排到列表最上面，所以按 peer 文本找行、点**标题**
    （不是行中心：中心会命中头像资料卡）比搜索稳定，也不会偏到搜索页/旧文件。
    """
    page = await teams_open()
    needle = (peer or "").strip()
    if not needle:
        return False
    frag = " ".join((snippet or "").split())[:12]
    for _attempt in range(4):
        for sel in _SEL["chat_item"]:
            try:
                rows = page.locator(sel)
                n = await rows.count()
            except Exception:                    # noqa: BLE001
                continue
            for i in range(min(n, 20)):
                row = rows.nth(i)
                try:
                    txt = (await row.inner_text(timeout=1500)) or ""
                except Exception:                # noqa: BLE001
                    continue
                if needle.lower()[:8] not in txt.lower():
                    continue
                # 同名可能有多个会话：优先带最后一条消息片段的那行
                if frag and frag not in " ".join(txt.split()):
                    continue
                clicked = False
                for tsel in _SEL["chat_title"]:
                    try:
                        t = row.locator(tsel).first
                        if await t.count() and await t.is_visible():
                            await t.click()
                            clicked = True
                            break
                    except Exception:            # noqa: BLE001
                        continue
                if not clicked:
                    try:
                        box = await row.bounding_box()
                        if box:
                            # 偏左、偏上：标题区域，避开头像/状态点
                            await page.mouse.click(box["x"] + 36,
                                                   box["y"] + box["height"] * 0.35)
                    except Exception:            # noqa: BLE001
                        pass
                await asyncio.sleep(2.0)
                head = await header_title(page)
                if head and needle.lower()[:8] in head.lower():
                    try:
                        await _settle(page, "message", rounds=1, delay=0.6)
                    except Exception:            # noqa: BLE001
                        pass
                    return True
        # 虚拟列表：往下滚一屏再找（目标通常在顶部，滚只是兜底）
        try:
            first = page.locator(_SEL["chat_item"][0]).first
            await first.hover()
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(0.8)
        except Exception:                        # noqa: BLE001
            break
    return False

async def open_chat(title_or_index: str | int, scroll_pages: int = 8) -> bool:
    """点开一个会话。给 index（list_chats 的返回值）或标题片段。

    Teams 的会话列表是**虚拟列表**（只渲染可见项），所以按标题找时要把列表往下翻；
    点完还要确认头部标题/消息区真的切过去了 —— 之前只看"点到了"，很容易读成上一个会话。
    """
    page = await teams_open()
    loc = None
    target_text = ""
    if isinstance(title_or_index, int) or str(title_or_index).isdigit():
        idx = int(title_or_index)
        for sel in _SEL["chat_item"]:
            try:
                cand = page.locator(sel).nth(idx)
                if await cand.count() and await cand.is_visible():
                    loc = cand
                    target_text = ((await cand.inner_text(timeout=1500)) or "")[:80]
                    break
            except Exception:                   # noqa: BLE001
                continue
    else:
        needle = str(title_or_index).strip()
        target_text = needle
        # 首选：逐字搜索（实测唯一可靠的切换方式）
        try:
            res = await api_search_typing(needle)
            if res.get("opened") and res.get("matched", True):
                await _settle(page, "message", rounds=2, delay=0.8)
                return True
            _b.log(f"open_chat 搜索未切到「{needle}」：{res.get('after')}")
        except Exception as exc:                 # noqa: BLE001
            _b.log(f"open_chat 搜索失败：{str(exc)[:120]}")
        for page_i in range(max(1, scroll_pages)):
            for sel in _SEL["chat_item"]:
                try:
                    cand = page.locator(sel).filter(has_text=needle).first
                    if await cand.count() and await cand.is_visible():
                        loc = cand
                        break
                except Exception:               # noqa: BLE001
                    continue
            if loc is not None:
                break
            # 没找到：把列表往下滚一屏再试（虚拟列表只有滚过才会渲染）
            try:
                first = page.locator(_SEL["chat_item"][0]).first
                if not await first.count():
                    break
                await first.hover()
                await page.mouse.wheel(0, 700)
                await asyncio.sleep(0.9)
            except Exception:                   # noqa: BLE001
                break
    if loc is None:
        return False
    before_head = await header_title(page)
    before_fp = await _pane_fingerprint(page)
    if not await _real_click(page, loc):
        return False
    # 等切换：头部标题变化 或 消息区指纹变化，最多 40s（Teams 首屏很慢）
    changed = False
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        await asyncio.sleep(1.2)
        head = await header_title(page)
        if before_head != head and head:
            changed = True
            break
        fp = await _pane_fingerprint(page)
        if before_fp and fp and fp != before_fp:
            changed = True
            break
        if not before_fp and fp:
            changed = True
            break
    if not changed:
        _b.log(f"open_chat 点击后没切换（头部仍是「{before_head[:30]}」）")
        return False
    await _settle(page, "message", rounds=3, delay=0.9)
    # 校验切换成功：头部标题里应出现目标名（按索引打开时跳过，因为拿不到名字）
    if target_text and not str(title_or_index).isdigit():
        try:
            head = ""
            for sel in ('[data-tid="entity-header"]', '[data-tid="chat-title"]'):
                el = page.locator(sel).first
                if await el.count():
                    head = (await el.inner_text(timeout=1500)) or ""
                    break
            if head and target_text.lower() not in head.lower():
                _b.log(f"open_chat 可能没切过去：目标「{target_text}」头部「{head[:40]}」")
        except Exception:                       # noqa: BLE001
            pass
    return True



_ME_PREFIX = re.compile(
    r"^(profile picture|profile photo|account manager|manage account|"
    r"settings and more|account|头像|个人资料|帐户|账户)"
    r"(\s+(for|of)\b)?[^\w\u4e00-\u9fff]*",
    re.I)

_SEL: dict[str, list[str]] = {
    # 「就绪」必须看到**聊天区的内容**：app shell 渲染完还可能是"We're setting things up"
    "ready": [
        '[data-tid="chat-list"]',
        '[role="treeitem"]',
        '[data-tid="chat-pane-item"]',
        '[contenteditable="true"]',
    ],
    "login_wall": [
        'input[type="email"]',
        '[data-tid="login-header"]',
        'text=Sign in to Teams',
    ],
    "chat_list": [
        '[data-tid="chat-list"]',
        '[data-tid="chat-pane-list"]',
        '[role="tree"][aria-label*="chat" i]',
    ],
    # 真机实测：会话项就是 chat-pane-item（同一元素里含参与者与最后一条消息预览）
    "chat_item": [
        '[data-tid="chat-pane-item"]',
        '[role="treeitem"]',
    ],
    "chat_title": [
        '[data-tid="chat-pane-item-title"]',
        '[data-tid="chat-pane-item-header"] [title]',
        'span[title]',
    ],
    "message_list": [
        '[data-tid="message-pane-list-runway"]',
        '[data-tid="message-pane-list-viewport"]',
        '[data-tid="chat-pane-message-list"]',
    ],
    "message": [
        '[data-tid="chat-pane-message"]',
        '[data-tid="message-body"]',
    ],
    "author": [
        '[data-tid="message-author-name"]',
        '[data-tid="message-avatar"]',
        '[data-tid="chat-pane-message-author"]',
    ],
    "text": [
        '[data-tid="chat-pane-message-body"]',
        '[data-tid="message-body-content"]',
        '[dir="auto"]',
    ],
    "composer": [
        '[data-tid="ckeditor"]',
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"]',
        '[role="textbox"]',
    ],
    "send": [
        '[data-tid="sendMessageCommands-send"]',
        'button[title*="Send" i]',
        'button[aria-label*="Send" i]',
    ],
    "time": [
        'time',
        '[data-tid="message-timestamp"]',
        '[data-tid="chat-pane-message-timestamp"]',
    ],
}

teams_page = None

_GANTRY_TAB_MARKERS = ("[GANTRY-AGENT]", "[CBP-AGENT]")

async def api_search_typing(name: str, page=None, wait: float = 5.0,
                            confirm: float = 45.0) -> dict[str, Any]:
    """逐字敲入搜索框（真实按键）→ 建议列表 → ↓/Enter → 校验头部标题。"""
    page = page or await teams_open()
    before = await header_title(page)
    out: dict[str, Any] = {"target": name, "before": (before or "")[:50]}
    inp = page.locator('input[data-tid="AUTOSUGGEST_INPUT"]').first
    if not await inp.count():
        return {**out, "error": "找不到搜索框"}
    try:
        await inp.click()
        await asyncio.sleep(0.5)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(name, delay=110)   # 真实按键，触发 autosuggest
        await asyncio.sleep(wait)
    except Exception as exc:                     # noqa: BLE001
        return {**out, "error": f"输入失败: {str(exc)[:120]}"}
    out["suggestion"] = await page.evaluate(r"""() => {
      const sels = ['[data-tid="ms-searchux-popup"]', '[role="listbox"]',
                    '[data-tid*="suggest" i]', '[data-tid*="SearchResult" i]'];
      for (const sel of sels) {
        const el = document.querySelector(sel);
        if (el && el.innerText) {
          return {sel, text: (el.innerText || '').replace(new RegExp('\\s+', 'g'), ' ')
                  .slice(0, 300)};
        }
      }
      return {sel: '', text: ''};
    }""")
    opened = False
    for how in ("enter", "down+enter", "down+down+enter"):
        try:
            downs = how.count("down")
            for _ in range(downs):
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(0.5)
            await page.keyboard.press("Enter")
            await asyncio.sleep(2.5)
        except Exception:                        # noqa: BLE001
            pass
        deadline = time.monotonic() + max(10.0, confirm)
        while time.monotonic() < deadline:
            head = await header_title(page)
            if head and head != before:
                out["after"] = head[:50]
                out["opened"] = True
                out["matched"] = name.lower()[:8] in head.lower()
                out["how"] = how
                return out
            await asyncio.sleep(1.5)
    out.setdefault("after", (await header_title(page) or "")[:50])
    out["opened"] = opened
    return out
