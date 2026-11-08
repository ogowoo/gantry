# -*- coding: utf-8 -*-
"""Power Automate maker 门户 UI 自动化。

复用服务现有的持久化 EdgeProfile（与 Copilot/Teams 同租户 SSO），
**不调用任何 Power Platform API**；导入通过网页 UI 完成。

当前提供：
- `probe()`：打开 make.powerautomate.com，判断登录/许可/首页状态并截图；
- `import_package()`：上传旧版导入包并完成导入（依赖页面结构，见函数说明）。
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

PA_URL = "https://make.powerautomate.com/"
PA_DOMAIN = "make.powerautomate.com"
_LOGIN_MARK = re.compile(r"(login\.microsoftonline\.com|login\.live\.com|/oauth2/|/common/login)", re.I)


async def _find_or_open(ctx: Any, url: str = PA_URL) -> Any:
    """在共享 ctx 里复用已有 maker 标签，没有就新建一个。"""
    try:
        for page in list(ctx.pages):
            try:
                if PA_DOMAIN in (page.url or ""):
                    return page
            except Exception:
                continue
    except Exception:
        pass
    return await ctx.new_page()


async def _goto(page: Any, url: str, timeout: int = 120000) -> None:
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as exc:
        if "ERR_ABORTED" not in str(exc):
            raise


async def _body_text(page: Any, timeout: int = 5000) -> str:
    try:
        return str(await page.inner_text("body", timeout=timeout) or "")
    except Exception:
        return ""


async def probe() -> dict[str, Any]:
    """打开 maker 门户并报告登录/许可状态（会截图到 data/pa-probe.png）。"""
    from . import bridge as b
    from .browser import ensure_browser_ready

    # Power Automate 不需要 Copilot 就绪：只要浏览器上下文/页面可用即可。
    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    ok = bool(await raw()) if callable(raw) else await ensure_browser_ready()
    if not ok or b.ctx is None:
        return {"ok": False, "error": "浏览器未就绪（ENSURE_BROWSER_RAW 未注册或启动失败）"}
    page = await _find_or_open(b.ctx)
    try:
        await page.bring_to_front()
    except Exception:
        pass
    try:
        if PA_DOMAIN not in (page.url or ""):
            await _goto(page, PA_URL)
    except Exception as exc:
        return {"ok": False, "error": f"打开 maker 门户失败: {exc}", "url": getattr(page, "url", "")}

    # 等待登录跳转结束 / 首页出现
    deadline = time.time() + 60
    last_url = ""
    while time.time() < deadline:
        try:
            last_url = page.url or ""
        except Exception:
            last_url = ""
        if _LOGIN_MARK.search(last_url):
            await asyncio.sleep(3)   # 让账号选择/密码框渲染完再截图
            break
        text = await _body_text(page, 3000)
        if any(k in text for k in ("My flows", "我的流", "Create", "创建", "Solutions", "解决方案",
                                    "Power Automate")):
            # 再多等两秒让 SPA 首屏稳定
            await asyncio.sleep(2)
            break
        await asyncio.sleep(2)

    url = ""
    try:
        url = page.url or ""
    except Exception:
        pass
    text = await _body_text(page, 8000)
    title = ""
    try:
        title = await page.title()
    except Exception:
        pass
    lower = text.lower()
    login_required = bool(_LOGIN_MARK.search(url)) or any(
        k in lower for k in ("sign in", "sign-in", "登录", "输入密码"))
    license_problem = any(k in lower for k in (
        "don't have a license", "do not have a license", "no license", "start free trial",
        "免费试用", "没有许可证", "upgrade your plan", "purchase a plan"))
    home = any(k in text for k in (
        "My flows", "我的流", "Create", "创建", "Solutions", "解决方案",
        "Templates", "模板", "Environments", "环境"))
    logged_in = bool(home and not login_required)

    png = ""
    try:
        out = b.BASE / "data" / "pa-probe.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out), full_page=False)
        png = str(out)
    except Exception as exc:
        b.log(f"Power Automate 探测截图失败: {exc}")

    return {
        "ok": True,
        "url": url,
        "title": title,
        "logged_in": logged_in,
        "login_required": login_required,
        "license_warning": license_problem,
        "home": home,
        "screenshot": png,
        "text_head": re.sub(r"\s+", " ", text)[:1200],
    }


_SNAPSHOT_JS = """
() => {
  const out = [];
  const sel = 'button, a, input, select, textarea, [role=button], [role=tab], [role=menuitem], [role=link]';
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= 300) break;
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width <= 0 || r.height <= 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      text: ((el.innerText || el.value || '') + '').trim().replace(/\\s+/g, ' ').slice(0, 100),
      aria: el.getAttribute('aria-label') || '',
      role: el.getAttribute('role') || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      href: el.getAttribute('href') || ''
    });
  }
  return out;
}
"""


async def login() -> dict[str, Any]:
    """打开 maker 门户；若停在“选择帐户”，自动点选第一个企业帐户。

    密码 / MFA 仍需用户在可见浏览器窗口里完成；本函数只减少一次点击。
    """
    from . import bridge as b

    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动"}
    page = await _find_or_open(b.ctx)
    try:
        await page.bring_to_front()
    except Exception:
        pass
    try:
        if PA_DOMAIN not in (page.url or ""):
            await _goto(page, PA_URL)
    except Exception:
        pass
    text = await _body_text(page, 5000)
    clicked = False
    if "Pick an account" in text or "选择帐户" in text or "选择账户" in text:
        # 登录页的账号磁贴是 div[role=button]，aria-label 形如
        # "Sign in with xxx@yyy work or school account"。
        try:
            loc = page.locator('div[role="button"]')
            if await loc.count():
                await loc.first.click(timeout=5000)
                clicked = True
        except Exception:
            clicked = False
        if not clicked:
            try:
                clicked = bool(await page.evaluate(
                    """() => {
                      for (const el of document.querySelectorAll('[role=button]')) {
                        const a = el.getAttribute('aria-label') || '';
                        if (a.includes('Sign in with')) { el.click(); return true; }
                      }
                      return false;
                    }"""))
            except Exception:
                clicked = False
    await asyncio.sleep(3)
    state = await probe()
    state["clicked_account"] = clicked
    return state


async def dom_snapshot() -> dict[str, Any]:
    """返回 maker 页面当前可见的按钮/链接/输入框（用于定位导入流程）。"""
    from . import bridge as b

    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动"}
    page = await _find_or_open(b.ctx)
    try:
        url = page.url or ""
        title = await page.title()
        items = await page.evaluate(_SNAPSHOT_JS)
    except Exception as exc:
        return {"ok": False, "error": f"读取页面失败: {exc}"}
    return {"ok": True, "url": url, "title": title, "items": items or []}
