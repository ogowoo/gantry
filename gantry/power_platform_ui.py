# -*- coding: utf-8 -*-
"""通用 Power Platform 门户 UI 操作（Playwright）。

一个插件覆盖多个 maker/admin 门户：Power Apps、Power Automate、Power Pages、
Copilot Studio、Power BI、Power Platform 管理中心。只允许白名单域名，
复用 EdgeProfile 的租户 SSO，不调用 Power Platform API。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlparse

from . import power_automate_ui as core

PORTALS: dict[str, dict[str, str]] = {
    "powerapps": {"url": "https://make.powerapps.com/",
                  "domain": "make.powerapps.com", "label": "Power Apps"},
    "powerautomate": {"url": "https://make.powerautomate.com/",
                      "domain": "make.powerautomate.com", "label": "Power Automate"},
    "powerpages": {"url": "https://make.powerpages.microsoft.com/",
                   "domain": "make.powerpages.microsoft.com", "label": "Power Pages"},
    "copilotstudio": {"url": "https://copilotstudio.microsoft.com/",
                      "domain": "copilotstudio.microsoft.com", "label": "Copilot Studio"},
    "powerbi": {"url": "https://app.powerbi.com/",
                "domain": "app.powerbi.com", "label": "Power BI"},
    "ppac": {"url": "https://admin.powerplatform.microsoft.com/",
             "domain": "admin.powerplatform.microsoft.com", "label": "Power Platform Admin"},
}
_DOMAINS = {v["domain"] for v in PORTALS.values()}
_ACTIVE: dict[str, str] = {}


def _resolve(portal: str) -> dict[str, str]:
    key = str(portal or "").strip().lower()
    if key in PORTALS:
        return {"key": key, **PORTALS[key]}
    url = str(portal or "").strip()
    host = (urlparse(url).hostname or "").lower()
    if host in _DOMAINS:
        for k, v in PORTALS.items():
            if v["domain"] == host:
                return {"key": k, **v}
    raise ValueError(f"不支持的 Power Platform 门户：{portal}。可用：{', '.join(PORTALS)}")


def _domain_of(url: str) -> str:
    return (urlparse(str(url or "")).hostname or "").lower()


async def _find_or_open(ctx: Any, entry: dict[str, str]) -> Any:
    domain = entry["domain"]
    try:
        for page in list(ctx.pages):
            try:
                if _domain_of(page.url) == domain:
                    _ACTIVE.update(entry)
                    return page
            except Exception:                    # noqa: BLE001
                continue
    except Exception:                            # noqa: BLE001
        pass
    page = await ctx.new_page()
    _ACTIVE.update(entry)
    await page.goto(entry["url"], wait_until="domcontentloaded", timeout=120000)
    return page


async def _current_page() -> tuple[Any | None, dict[str, Any] | None]:
    from . import bridge as b

    if b.ctx is None:
        return None, {"ok": False, "error": "浏览器未启动"}
    domain = _ACTIVE.get("domain", "")
    try:
        for page in list(b.ctx.pages):
            try:
                if domain and _domain_of(page.url) == domain:
                    return page, None
                if not domain and _domain_of(page.url) in _DOMAINS:
                    return page, None
            except Exception:                    # noqa: BLE001
                continue
    except Exception:                            # noqa: BLE001
        pass
    if domain:
        return None, {"ok": False, "error": "当前门户页面已关闭；请重新 pp_open"}
    return None, {"ok": False, "error": "没有打开的 Power Platform 门户；先 pp_open"}


async def _ensure_browser() -> dict[str, Any] | None:
    from . import bridge as b

    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动（ENSURE_BROWSER_RAW 不可用）"}
    return None


async def probe(portal: str) -> dict[str, Any]:
    """打开门户并报告登录/许可状态（会截图到 data/pp-probe-<name>.png）。"""
    from . import bridge as b

    try:
        entry = _resolve(portal)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    err = await _ensure_browser()
    if err:
        return err
    page = await _find_or_open(b.ctx, entry)
    try:
        await page.bring_to_front()
    except Exception:                            # noqa: BLE001
        pass
    deadline = time.time() + 60
    text = ""
    while time.time() < deadline:
        try:
            if core._LOGIN_MARK.search(page.url or ""):
                await asyncio.sleep(3)
                break
            text = await core._body_text(page, 3000)
            if any(k in text for k in ("Apps", "应用", "Create", "创建", "Solutions",
                                        "解决方案", "Power", "Copilot", "Reports", "报表")):
                await asyncio.sleep(2)
                break
        except Exception:                        # noqa: BLE001
            pass
        await asyncio.sleep(2)
    try:
        url = page.url or ""
        title = await page.title()
    except Exception:                            # noqa: BLE001
        url, title = "", ""
    text = await core._body_text(page, 8000)
    lower = text.lower()
    login_required = bool(core._LOGIN_MARK.search(url)) or any(
        k in lower for k in ("sign in", "sign-in", "登录", "输入密码"))
    license_problem = any(k in lower for k in (
        "don't have a license", "no license", "start free trial", "免费试用",
        "没有许可证", "upgrade your plan", "purchase a plan"))
    home = any(k in text for k in ("Apps", "应用", "Create", "创建", "Solutions",
                                    "解决方案", "Power", "Reports", "报表"))
    if home and not login_required:
        try:
            await core.save_auth_state(b.ctx)
        except Exception:                        # noqa: BLE001
            pass
    png = ""
    try:
        out = b.BASE / "data" / f"pp-probe-{entry['key']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out), full_page=False)
        png = str(out)
    except Exception as exc:                     # noqa: BLE001
        b.log(f"Power Platform 探测截图失败: {exc}")
    return {"ok": True, "portal": entry["key"], "label": entry["label"],
            "url": url, "title": title,
            "logged_in": bool(home and not login_required),
            "login_required": login_required, "license_warning": license_problem,
            "home": home, "screenshot": png,
            "text_head": re.sub(r"\s+", " ", text)[:1200]}


def active_portal() -> str:
    """当前最近一次打开/探测的门户 key（没有则空串）。"""
    return str(_ACTIVE.get("key") or "")


async def open_portal(portal: str, path: str = "/", wait: float = 3.0) -> dict[str, Any]:
    """打开指定 Power Platform 门户（白名单）并可带相对路径。"""
    from . import bridge as b

    try:
        entry = _resolve(portal)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    err = await _ensure_browser()
    if err:
        return err
    page = await _find_or_open(b.ctx, entry)
    target = path if str(path).startswith("http") else entry["url"].rstrip("/") + "/" + str(path or "/").lstrip("/")
    if str(target).startswith("http"):
        host = (urlparse(target).hostname or "").lower()
        if host not in _DOMAINS:
            return {"ok": False, "error": f"目标域名不在白名单：{host}"}
    try:
        await page.goto(target, wait_until="domcontentloaded", timeout=120000)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"导航失败: {exc}"}
    await asyncio.sleep(max(0.5, wait))
    return await core._snap(page, portal=entry["key"])


async def dom_snapshot() -> dict[str, Any]:
    page, err = await _current_page()
    if err:
        return err
    return await core._snap(page)


async def click_text(text: str, role: str = "", wait: float = 2.0) -> dict[str, Any]:
    page, err = await _current_page()
    if err:
        return err
    target = str(text or "").strip()
    if not target:
        return {"ok": False, "error": "text 不能为空"}
    clicked, error = False, ""
    try:
        frames = list(page.frames)
    except Exception:                            # noqa: BLE001
        frames = [page]
    pat = re.compile(re.escape(target), re.I)
    for f in frames:
        locs = []
        if role:
            locs.append(f.get_by_role(role, name=pat))
        locs.append(f.get_by_text(pat))
        for loc in locs:
            try:
                if await loc.count():
                    await loc.first.click(timeout=8000)
                    clicked = True
                    break
            except Exception as exc:             # noqa: BLE001
                error = str(exc)
        if clicked:
            break
    if not clicked and not error:
        try:
            clicked = bool(await page.evaluate(
                """(label) => {
                  const all = [...document.querySelectorAll('button,a,[role=menuitem],[role=button]')];
                  const el = all.find(e => ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')).trim().includes(label));
                  if (el) { el.click(); return true; }
                  return false;
                }""", target))
        except Exception as exc:                 # noqa: BLE001
            error = str(exc)
    await asyncio.sleep(max(0.5, wait))
    snap = await core._snap(page, clicked=clicked)
    if error:
        snap["click_error"] = error
    return snap


async def fill_text(label: str = "", value: str = "", index: int = 0,
                    wait: float = 0.5) -> dict[str, Any]:
    page, err = await _current_page()
    if err:
        return err
    target_label = str(label or "").strip()
    if not target_label:
        return {"ok": False, "error": "label 不能为空"}
    escaped = target_label.replace('"', '\\"')
    candidates = []
    for f in page.frames:
        for factory in (
            lambda: f.get_by_label(target_label),
            lambda: f.get_by_placeholder(target_label),
            lambda: f.get_by_role("textbox", name=re.compile(re.escape(target_label), re.I)),
            lambda: f.locator(f'[aria-label*="{escaped}"]'),
        ):
            try:
                candidates.append(factory())
            except Exception:                    # noqa: BLE001
                pass
    last = ""
    for loc in candidates:
        try:
            count = await loc.count()
            if not count:
                continue
            target = loc.nth(min(max(0, int(index)), count - 1))
            if not await target.is_visible():
                continue
            await target.fill(str(value))
            await asyncio.sleep(max(0.1, wait))
            return await core._snap(page, filled=True, label=target_label)
        except Exception as exc:                 # noqa: BLE001
            last = str(exc)
    return await core._snap(page, filled=False, label=target_label,
                            error=last or "没有找到可填写的输入框")


async def press_key(key: str, wait: float = 0.5) -> dict[str, Any]:
    page, err = await _current_page()
    if err:
        return err
    combo = str(key or "").strip()
    if not combo:
        return {"ok": False, "error": "key 不能为空"}
    try:
        await page.keyboard.press(combo)
        await asyncio.sleep(max(0.1, wait))
        return await core._snap(page, pressed=combo)
    except Exception as exc:                     # noqa: BLE001
        return await core._snap(page, pressed=False, error=str(exc)[:300])


async def select_option(label: str = "", value: str = "", index: int = 0) -> dict[str, Any]:
    page, err = await _current_page()
    if err:
        return err
    last = ""
    for f in page.frames:
        try:
            loc = f.locator("select")
            count = await loc.count()
            if not count:
                continue
            target = loc.nth(min(max(0, int(index)), count - 1))
            if label:
                await target.select_option(label=label, timeout=5000)
            elif value:
                await target.select_option(value=value, timeout=5000)
            else:
                return await core._snap(page, selected=False, error="label/value 至少给一个")
            await asyncio.sleep(1)
            return await core._snap(page, selected=True)
        except Exception as exc:                 # noqa: BLE001
            last = str(exc)
    return await core._snap(page, selected=False, error=last or "没有找到 select")
