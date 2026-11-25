# -*- coding: utf-8 -*-
"""WebChat 后端：把网页版聊天包装成 OpenAI 兼容提供商。

思路与 OmniRoute/CLIProxy 类工具一致：用你已登录的浏览器会话发消息、抓回答，
绕开 API 计费。Gantry 已有 Playwright + 持久化 EdgeProfile 的整套基础设施
（browser.py，Copilot 自动化同源），所以 webchat 直接复用：

- 登录态与 Copilot 自动化共享同一个 EdgeProfile（受管浏览器里登录一次即可）；
- 登录入口：设置页「打开登录页」在受管浏览器里打开站点；或用
  scripts/login.py --url=https://chatgpt.com/（服务停止时）。
- 选择器集中在本文件的 SITES 注册表：网页改版挂的是选择器，加厂商是填表。

⚠️ 风险提示：厂商消费条款不允许自动化网页版，账号有风控可能；网页结构会
变（选择器脆）；回答有流式等待，吞吐远低于 API。只建议在无 API key 时兜底。
"""
import asyncio
import logging
import time
from typing import Any, cast

log = logging.getLogger("gantry.webchat")

# 每个站点的受管页面（进程内复用，关闭后自动重建）
_PAGES: dict[str, Any] = {}

# 选择器注册表。每个站点：
#   url          站点入口
#   input        输入框选择器（contenteditable 或 textarea）
#   login_ok_js  JS 表达式：返回 true 表示已登录（一般在场 input 即可）
#   send_key     发送键
#   answer_js    JS 表达式：取最后一条助手消息文本（"" 表示还没有）
#   busy_js      JS 表达式：true 表示正在生成
WEBCHAT_SITES: dict[str, dict[str, Any]] = {
    "chatgpt-web": {
        "label": "ChatGPT（网页版）",
        "url": "https://chatgpt.com/",
        "input": "#prompt-textarea",
        "login_ok_js": "!!document.querySelector('#prompt-textarea')",
        "send_key": "Enter",
        # data-message-author-role 是 ChatGPT 的稳定语义属性，比 class 名可靠
        "answer_js": (
            "(() => { const els = document.querySelectorAll("
            "'[data-message-author-role=\"assistant\"] .markdown');"
            " return els.length ? els[els.length-1].innerText : ''; })()"
        ),
        "busy_js": (
            "!!document.querySelector('button[data-testid=\"stop-button\"]') "
            "|| [...document.querySelectorAll('button')].some(b => "
            "(b.getAttribute('aria-label')||'').match(/Stop generating|停止生成/i))"
        ),
        "new_chat_js": (
            "(() => { const b = document.querySelector('a[href=\"/\"], "
            "button[data-testid=\"create-new-chat-button\"]'); "
            " if (b) { b.click(); return true; } return false; })()"
        ),
        "models": ["gpt-5.1", "gpt-5", "gpt-5-mini"],
    },
}

_SITE_TIMEOUT = 300        # 单次生成上限（网页慢，给足）
_POLL = 0.6
_STABLE_TIMES = 3          # 连续 N 次采样文本不变视为生成结束


def site_spec(site: str) -> dict[str, Any]:
    spec = WEBCHAT_SITES.get(site)
    if not spec:
        raise KeyError(f"未知 webchat 站点：{site}")
    return spec


async def ensure_site_page(site: str, wait_load: bool = True):
    """在共享持久化上下文里打开/复用站点页面。

    wait_load=False 时导航放后台跑（webchat-open 登录入口用）：接口秒回，
    窗口弹出就是可见进度，避免前端对着 120s 的 goto 干等看不到在干什么。
    """
    from . import bridge as b      # 浏览器状态都在 bridge（ctx/pw）
    from . import browser          # 启动逻辑在 browser

    spec = site_spec(site)
    if not await browser.ensure_browser_ready():
        raise RuntimeError("受管浏览器不可用（未安装 playwright 或启动失败）")
    page = _PAGES.get(site)
    try:
        if page is not None and not page.is_closed():
            return page
    except Exception:                                    # noqa: BLE001
        pass
    ctx = cast(Any, b.ctx)   # bridge 把 ctx 声明为 None 再运行期赋值，Pyright 会缩成 Never
    if ctx is None:
        raise RuntimeError("受管浏览器上下文未建立")
    page = await ctx.new_page()
    if wait_load:
        await page.goto(spec["url"], wait_until="domcontentloaded", timeout=120000)
    else:
        asyncio.get_running_loop().create_task(_goto_bg(page, spec["url"]))
    _PAGES[site] = page
    return page


async def _goto_bg(page: Any, url: str) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    except Exception:                                    # noqa: BLE001
        # 后台导航失败不算致命：页面已在，用户可刷新或重开
        log.info("webchat 后台导航失败: %s", url)


async def check_login(site: str, timeout: float = 60.0) -> tuple[bool, str]:
    """登录态探测：输入框在场 = 已登录。返回 (ok, error)。"""
    spec = site_spec(site)
    try:
        page = await asyncio.wait_for(ensure_site_page(site), timeout)
        ok = await asyncio.wait_for(
            page.evaluate(spec["login_ok_js"]), timeout)
        if ok:
            return True, ""
        return False, (f"{spec['label']} 未登录。请在设置页点「打开登录页」，"
                       "在受管浏览器窗口里完成登录后再测试。")
    except Exception as exc:                             # noqa: BLE001
        return False, f"登录态检查失败：{str(exc)[:200]}"


async def chat(site: str, prompt: str, timeout: float = _SITE_TIMEOUT) -> str:
    """发一条消息并等待生成结束，返回最后一条助手回复的文本。"""
    spec = site_spec(site)
    page = await ensure_site_page(site)
    if not await page.evaluate(spec["login_ok_js"]):
        raise RuntimeError(f"{spec['label']} 未登录，无法发送。请先完成登录。")

    inp = page.locator(spec["input"]).first
    await inp.click(timeout=30000)
    # contenteditable 用 press_sequentially 会逐键触发 JS 编辑器；ChatGPT 的
    # ProseMirror 对 fill() 不敏感，先 fill 再补一次键盘输入更稳。
    await inp.fill(prompt)
    await inp.press("End")
    await inp.press(spec["send_key"])

    deadline = time.monotonic() + timeout
    last_text, stable = "", 0
    await asyncio.sleep(1.0)           # 让「正在生成」状态先出现
    while time.monotonic() < deadline:
        busy = bool(await page.evaluate(spec["busy_js"]))
        text = str(await page.evaluate(spec["answer_js"]) or "")
        if text and not busy:
            if text == last_text:
                stable += 1
                if stable >= _STABLE_TIMES:
                    return text.strip()
            else:
                stable, last_text = 0, text
        await asyncio.sleep(_POLL)
    if last_text:
        log.warning("webchat %s 等待超时，返回已生成的部分回答（%d 字）",
                    site, len(last_text))
        return last_text.strip()
    raise RuntimeError(f"{spec['label']} 生成超时（{timeout:.0f}s），没有拿到回答。")


def flatten_messages(messages: list[dict[str, Any]],
                     response_schema: dict[str, Any] | None = None) -> str:
    """把 OpenAI 消息列表压成一段网页提示词（网页版没有角色通道）。"""
    parts: list[str] = []
    for m in messages:
        role = m.get("role") or "user"
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content
                               if isinstance(p, dict))
        text = str(content or "").strip()
        if not text:
            continue
        parts.append(text if role in ("user", "system")
                     else f"[助手此前的回答]\n{text}")
    if response_schema:
        import json
        parts.append("只输出一个 JSON 对象，不要输出任何其他文字，"
                     "必须符合这个 JSON Schema：\n"
                     + json.dumps(response_schema, ensure_ascii=False))
    return "\n\n".join(parts)
