"""Outlook 网页版（OWA）自动化后端（从 bridge.py 拆出）。

New Outlook 无 COM 接口、EWS 2026 起被微软全局禁用、Graph 需管理员授权；
OWA 复用桥接的浏览器与 SSO 登录态。ctx 经模块 __getattr__ 惰性取
bridge.ctx——headless 切换会重建上下文，直接 import 变量会拿到死引用。
"""

import asyncio
import re
import time

from . import bridge as _b
from .bridge import diagnostics


def __getattr__(name):
    if name == "ctx":
        return _b.ctx
    raise AttributeError(name)


OWA_URL = _b.CFG.get("outlook_url", "https://outlook.office.com/mail/")


owa_page = None

# 页面互斥锁：owa_search_mails / read / send 等会操作共享 owa_page 的
# 键盘焦点与 DOM，复合操作必须整体持锁。尤其「先 search 再点击索引」的
# 两步（OWABackend.read）必须在同一把锁内完成，否则并发的搜索会把
# option 列表换掉，点到错误的邮件。用法参考 teams_common._PAGE_LOCK。
_OWA_LOCK = asyncio.Lock()


def _kql_quote(keyword: str) -> str:
    """KQL subject:"..." 共用的去引号转义：read 与 search 走同一个 helper，
    避免一侧转义一侧不转义导致同一 keyword 搜索结果不一致。"""
    return str(keyword).replace('"', "")


async def _owa_visible(page, selectors):
    ctx = _b.ctx  # 同上：headless 切换会重建上下文
    for selector in selectors:
        loc = page.locator(selector)
        for i in range(await loc.count() - 1, -1, -1):
            try:
                if await loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:
                pass
    return None


async def _owa_open_locked() -> None:
    """打开/复用 OWA 标签页，等待 SSO 回跳与列表渲染完成。

    持锁前提：调用方必须持有 _OWA_LOCK（owa_open / 各复合操作入口）。
    """
    # ctx 是懒启动的：先确保浏览器起来，否则 _b.ctx 还是 None，
    # 直接 new_page 会 AttributeError。参考 teams_dom.teams_open。
    try:
        _raw = getattr(_b, "ENSURE_BROWSER_RAW", None)
        if callable(_raw):
            await _raw()
        else:
            await _b.ensure_browser_ready()
    except Exception as exc:                     # noqa: BLE001
        raise RuntimeError(f"启动浏览器失败：{exc}") from exc
    ctx = _b.ctx
    if ctx is None:
        raise RuntimeError("浏览器上下文未就绪（ctx 为 None），无法打开 Outlook 网页版")
    global owa_page
    if owa_page is None or owa_page.is_closed():
        owa_page = await ctx.new_page()
    if "outlook." not in (owa_page.url or ""):
        await owa_page.goto(OWA_URL, wait_until="domcontentloaded", timeout=120000)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if "outlook." in (owa_page.url or ""):
                box = await _owa_visible(owa_page, [
                    'input[type="search"]',
                    'input[aria-label*="Search" i]',
                    'input[aria-label*="搜索"]',
                ])
                if box:
                    return
        except Exception:
            pass
        await asyncio.sleep(2)
    await diagnostics("owa_not_ready")
    raise RuntimeError("Outlook 网页版加载超时（SSO 未完成或页面结构变化）")


async def owa_open() -> None:
    """打开/复用 OWA 标签页（持 _OWA_LOCK 整体互斥）。"""
    async with _OWA_LOCK:
        await _owa_open_locked()


async def _owa_search_locked(query: str, max_count: int) -> list[dict]:
    """用 OWA 搜索框跑 KQL（isread:false / subject: 等），解析结果列表。

    持锁前提：调用方必须已调用 _owa_open_locked() 并持有 _OWA_LOCK。
    """
    box = await _owa_visible(owa_page, [
        'input[type="search"]', 'input[aria-label*="Search" i]', 'input[aria-label*="搜索"]',
    ])
    if not box:
        raise RuntimeError("找不到 Outlook 搜索框")
    await box.click()
    await box.fill(query)
    await box.press("Enter")
    await asyncio.sleep(2.5)  # 结果渲染
    # 邮件列表项：role=option，aria-label 未读项通常以 Unread/未读 开头
    items = []
    options = owa_page.locator('[role="option"]')
    count = await options.count()
    for i in range(min(count, max_count)):
        item = options.nth(i)
        try:
            if not await item.is_visible():
                continue
            label = (await item.get_attribute("aria-label")) or ""
            text = (await item.inner_text()).strip()
            lines = [x.strip() for x in text.splitlines() if x.strip()]
            items.append({
                "sender": lines[0] if lines else "",
                "subject": lines[1] if len(lines) > 1 else label,
                "preview": lines[2] if len(lines) > 2 else "",
                "received": lines[-1] if lines else "",
                "unread": bool(re.match(r"^(unread|未读)", label, re.I)),
                "_index": i,
            })
        except Exception:
            continue
    return items


async def owa_search_mails(query: str, max_count: int) -> list[dict]:
    """用 OWA 搜索框搜索（持 _OWA_LOCK，内部含 owa_open）。"""
    async with _OWA_LOCK:
        await _owa_open_locked()
        return await _owa_search_locked(query, max_count)


async def _owa_read_current_locked() -> str:
    """读取阅读窗格正文。持锁前提：调用方持有 _OWA_LOCK。"""
    pane = await _owa_visible(owa_page, [
        '[id*="ReadingPane"]',
        'div[role="main"]',
        '[aria-label*="reading pane" i]',
        '[aria-label*="阅读窗格"]',
    ])
    if not pane:
        return ""
    return (await pane.inner_text()).strip()


async def owa_read_current() -> str:
    """读取阅读窗格正文（持 _OWA_LOCK 整体互斥）。"""
    async with _OWA_LOCK:
        # 入口兜底：owa_page 可能还没打开或被外部关掉（如浏览器重启），
        # 与 search/send 一样先确保页面就绪，否则 _owa_read_current_locked
        # 会在死引用上静默返回空串。
        if owa_page is None or owa_page.is_closed():
            await _owa_open_locked()
        return await _owa_read_current_locked()


async def _owa_click_and_read(target: dict) -> str:
    """按目标邮件点击列表行并读取阅读窗格正文。

    点击前用 target.subject 按文本重新定位行（列表在搜索渲染期间可能重排，
    裸 nth(_index) 会点错）；读取失败/为空时返回空串，由调用方决定是否重搜。
    持锁前提：调用方持有 _OWA_LOCK 且已 _owa_open_locked()。
    """
    global owa_page
    subj = (target.get("subject") or "").strip()
    row = owa_page.locator('[role="option"]').nth(target["_index"])
    if subj:
        try:
            by_text = owa_page.locator('[role="option"]', has_text=subj)
            if await by_text.count() > 0:
                row = by_text.first
        except Exception:
            pass                                # 文本定位失败退回 nth(_index)
    await row.click()
    await asyncio.sleep(1.2)
    return await _owa_read_current_locked()


class OWABackend:
    """tools.py 的 Outlook 工具后端接口（owa 实现）。"""

    async def unread(self, max_count: int) -> dict:
        items = await owa_search_mails("isread:false", max_count)
        for it in items:
            it.pop("_index", None)
        return {"unread_returned": len(items), "items": items, "backend": "owa"}

    async def read(self, keyword: str) -> dict:
        # 「search → 点击索引 → 读正文」是复合操作，必须同一把锁内完成，
        # 否则并发的搜索会换掉 option 列表，点到错误的邮件。
        async with _OWA_LOCK:
            await _owa_open_locked()
            items = await _owa_search_locked(
                f'subject:"{_kql_quote(keyword)}"' if keyword else "isread:false", 10)
            target = None
            for it in items:
                if keyword and keyword.lower() in (it.get("subject") or "").lower():
                    target = it
                    break
            if target is None and items:
                target = items[0]
            if target is None:
                raise RuntimeError(f"没有找到主题包含「{keyword}」的邮件")
            body = await _owa_click_and_read(target)
            subj = (target.get("subject") or "").strip()
            if subj and subj.lower() not in body.lower():
                # 阅读窗格标题与目标不一致：option 列表可能被并发操作换过，
                # 重搜一次再点，避免把别封邮件的正文当成目标返回。
                _b.log(f"OWA read：点击后阅读窗格与「{subj}」不一致，重搜一次")
                items = await _owa_search_locked(
                    f'subject:"{_kql_quote(keyword)}"' if keyword else "isread:false", 10)
                target = None
                for it in items:
                    if keyword and keyword.lower() in (it.get("subject") or "").lower():
                        target = it
                        break
                if target is None and items:
                    target = items[0]
                if target is not None:
                    body = await _owa_click_and_read(target)
        # AUD-P2：重搜后可能仍无结果（邮件被移走/搜索 UI 未返回），target 为 None
        # 时 .pop 直接 AttributeError——改成明确的"找不到"错误。
        if target is None:
            raise RuntimeError(f"没有找到主题包含「{keyword}」的邮件")
        target.pop("_index", None)
        target["body"] = body[:20000]
        target["truncated"] = len(body) > 20000
        return target

    async def search(self, keyword: str, max_count: int) -> dict:
        safe = _kql_quote(keyword)
        items = await owa_search_mails(f'subject:"{safe}"', max_count)
        for it in items:
            it.pop("_index", None)
        return {"keyword": keyword, "returned": len(items), "items": items, "backend": "owa"}

    async def send(self, to: str, subject: str, body: str, cc: str = "") -> dict:
        # 新建邮件 → 填收件人/主题/正文 → 发送：全程占用 owa_page 键盘焦点，
        # 必须整体持锁，否则并发搜索/读信会把焦点抢走的。
        async with _OWA_LOCK:
            await _owa_open_locked()
            return await self._send_locked(to, subject, body, cc=cc)

    async def _send_locked(self, to: str, subject: str, body: str, cc: str = "") -> dict:
        btn = await _owa_visible(owa_page, [
            'button[aria-label*="New mail" i]', 'button[aria-label*="新建邮件"]',
            'button[aria-label*="New message" i]', 'button[aria-label*="撰写"]',
        ])
        if not btn:
            raise RuntimeError("找不到新建邮件按钮")
        await btn.click()
        await asyncio.sleep(1.2)
        to_box = await _owa_visible(owa_page, [
            'input[aria-label*="To" i]', 'input[aria-label*="收件人"]',
        ])
        if not to_box:
            raise RuntimeError("找不到收件人输入框")
        await to_box.click()
        await to_box.press_sequentially(to)
        await asyncio.sleep(.5)
        await owa_page.keyboard.press("Enter")
        if cc:
            await owa_page.keyboard.press("Tab")
            await owa_page.keyboard.insert_text(cc)
            await owa_page.keyboard.press("Enter")
        subject_box = await _owa_visible(owa_page, [
            'input[aria-label*="subject" i]', 'input[aria-label*="主题"]',
            'input[placeholder*="subject" i]', 'input[placeholder*="主题"]',
        ])
        if subject_box:
            await subject_box.click()
            await subject_box.fill(subject)
        body_box = await _owa_visible(owa_page, [
            '[contenteditable="true"][aria-label*="body" i]',
            '[contenteditable="true"][aria-label*="正文"]',
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"]',
        ])
        if body_box:
            await body_box.click()
            await body_box.press_sequentially(body)
        send_btn = await _owa_visible(owa_page, [
            'button[aria-label*="Send" i]', 'button[aria-label*="发送"]',
        ])
        if not send_btn:
            raise RuntimeError("找不到发送按钮")
        await send_btn.click()
        # AUD-P2（审核 ⑥-4）：点击发送≠发送成功。收件人未解析/校验弹框、
        # 点击被吞、网络失败时 UI 不会立即报错。等待撰写窗格关闭作为成功信号；
        # 窗格还在时如实返回 unknown，而不是谎报已发送。
        sent = False
        try:
            for _ in range(10):
                await asyncio.sleep(0.6)
                if await self._compose_pane_gone(to_box):
                    sent = True
                    break
        except Exception:                        # noqa: BLE001
            sent = False
        return {"sent": sent, "unknown": not sent, "to": to, "subject": subject,
                "backend": "owa"}

    async def _compose_pane_gone(self, probe) -> bool:
        """撰写窗格是否已关闭（收件人输入框随之消失）。"""
        if owa_page is None or probe is None:
            return True
        try:
            return await probe.count() == 0
        except Exception:                        # noqa: BLE001
            return False


OWA_BACKEND = OWABackend()


