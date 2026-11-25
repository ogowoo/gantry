# -*- coding: utf-8 -*-
"""实时视图：把受管浏览器的页面画面流式投回 Gantry UI。

场景：让 Agent 打开一个网站做爬虫/分析时，用户在对话页右侧的「实时视图」
面板里同步看到浏览器画面，边分析边继续对话。

实现：Playwright 的 CDP 会话 `Page.startScreencast` 持续推 JPEG 帧（Edge
内核同样支持）。帧只保留最新一张——慢消费者丢帧不积压；页面静止时没有新帧，
SSE 侧发心跳保活。
"""
import asyncio
import logging
import time
from typing import Any, AsyncGenerator, cast

log = logging.getLogger("gantry.liveview")

# open_url 最近一次的记录：前端轮询 /v1/live/pages 时据此自动展开面板
_last_open: dict[str, Any] = {"url": "", "at": 0.0}

# screencast 参数：960 宽足够面板看，JPEG 60 平衡清晰度与带宽
_CAST = {"format": "jpeg", "quality": 60,
         "maxWidth": 960, "maxHeight": 640, "everyNthFrame": 2}


async def _ctx():
    from . import bridge as b
    from . import browser
    if not await browser.ensure_browser_ready():
        raise RuntimeError("受管浏览器不可用（未安装 playwright 或启动失败）")
    ctx = b.ctx
    if ctx is None:
        raise RuntimeError("受管浏览器上下文未建立")
    # b.ctx 注解是 BrowserContext|None；None 检查后 Pyright 把属性链缩成 Never，
    # 运行时由 ensure_browser_ready 保证真实对象，cast 回 Any
    return cast(Any, b.ctx)


async def list_pages() -> dict[str, Any]:
    """受管浏览器里当前打开的页面 + 最近一次 open_url 记录。"""
    ctx = await _ctx()
    pages = []
    for i, p in enumerate(ctx.pages):
        try:
            if p.is_closed():
                continue
            pages.append({"index": i,
                          "title": (await p.title())[:80],
                          "url": p.url or ""})
        except Exception:                                # noqa: BLE001
            continue
    return {"pages": pages, "last_open": dict(_last_open)}


async def open_url(url: str) -> dict[str, Any]:
    """在受管浏览器新开页访问 URL（实时视图与 Agent 工具共用的入口）。"""
    url = str(url or "").strip()
    # 这个端点本质是“让浏览器去访问某地址”，非 http/https 一律挡掉
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("只支持 http/https 链接")
    ctx = await _ctx()
    page = await ctx.new_page()
    await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    _last_open.update({"url": url, "at": time.time()})
    log.info("liveview 打开页面: %s", url)
    return {"ok": True, "url": url, "title": (await page.title())[:80]}


def last_open() -> dict[str, Any]:
    return dict(_last_open)


async def stream_frames(index: int) -> AsyncGenerator[str | None, None]:
    """对第 index 个页面起 CDP screencast，异步产出 base64 JPEG 帧。

    产出 None 表示心跳（页面静止无新帧），路由层转成 SSE 注释行保活。
    连接断开（生成器关闭）时自动 stopScreencast 并 detach。
    """
    ctx = await _ctx()
    # 序号口径与 list_pages 一致：都是 ctx.pages 里的原始下标（关掉的页跳过）
    if index < 0 or index >= len(ctx.pages) or ctx.pages[index].is_closed():
        raise KeyError(f"页面序号无效：{index}")
    page = ctx.pages[index]
    cdp = await ctx.new_cdp_session(page)
    latest: dict[str, Any] = {"data": None}
    ev = asyncio.Event()

    def _on_frame(params: dict) -> None:
        latest["data"] = params.get("data")
        sid = params.get("sessionId")
        # screencast 协议要求逐帧 ack，否则后续帧停发
        asyncio.get_running_loop().create_task(_ack(cdp, sid))
        ev.set()

    cdp.on("Page.screencastFrame", _on_frame)
    await cdp.send("Page.startScreencast", dict(_CAST))
    try:
        while True:
            try:
                await asyncio.wait_for(ev.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield None                     # 页面静止：心跳
                continue
            ev.clear()
            data, latest["data"] = latest["data"], None
            if data:
                yield data
    finally:
        for call in ("Page.stopScreencast",):
            try:
                await cdp.send(call)
            except Exception:                            # noqa: BLE001
                pass
        try:
            await cdp.detach()
        except Exception:                                # noqa: BLE001
            pass


async def _ack(cdp: Any, session_id: Any) -> None:
    if session_id is None:
        return
    try:
        await cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
    except Exception:                                    # noqa: BLE001
        pass
