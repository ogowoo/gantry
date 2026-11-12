# -*- coding: utf-8 -*-
"""Power Apps maker 门户 UI 自动化（Playwright）。

复用 `power_automate_ui` 的通用控件采集/快照逻辑，只把门户地址与
登录/许可文案换成 make.powerapps.com。不调用 Power Platform API。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from . import power_automate_ui as core

POWERAPPS_URL = "https://make.powerapps.com/"
POWERAPPS_DOMAIN = "make.powerapps.com"


async def _find_or_open(ctx: Any) -> Any:
    try:
        for page in list(ctx.pages):
            try:
                if POWERAPPS_DOMAIN in (page.url or ""):
                    return page
            except Exception:                    # noqa: BLE001
                continue
    except Exception:                            # noqa: BLE001
        pass
    return await ctx.new_page()


async def _ensure_page() -> tuple[Any | None, dict[str, Any] | None]:
    from . import bridge as b

    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return None, {"ok": False, "error": "浏览器未启动"}
    return await _find_or_open(b.ctx), None


async def probe() -> dict[str, Any]:
    """打开 Power Apps maker 门户并报告登录/许可状态。"""
    from . import bridge as b

    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动"}
    page = await _find_or_open(b.ctx)
    try:
        await page.bring_to_front()
    except Exception:                            # noqa: BLE001
        pass
    try:
        if POWERAPPS_DOMAIN not in (page.url or ""):
            await page.goto(POWERAPPS_URL, wait_until="domcontentloaded",
                            timeout=120000)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"打开 Power Apps 门户失败: {exc}"}

    deadline = time.time() + 60
    text = ""
    while time.time() < deadline:
        try:
            url = page.url or ""
            if core._LOGIN_MARK.search(url):
                await asyncio.sleep(3)
                break
            text = await core._body_text(page, 3000)
            if any(k in text for k in ("Apps", "应用", "Create", "创建",
                                        "Solutions", "解决方案", "Power Apps")):
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
        "don't have a license", "do not have a license", "no license",
        "start free trial", "免费试用", "没有许可证", "upgrade your plan",
        "purchase a plan"))
    home = any(k in text for k in ("Apps", "应用", "Create", "创建",
                                    "Solutions", "解决方案", "Power Apps"))
    if home and not login_required:
        try:
            await core.save_auth_state(b.ctx)
        except Exception:                        # noqa: BLE001
            pass
    png = ""
    try:
        out = b.BASE / "data" / "powerapps-probe.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(out), full_page=False)
        png = str(out)
    except Exception as exc:                     # noqa: BLE001
        b.log(f"Power Apps 探测截图失败: {exc}")
    return {"ok": True, "url": url, "title": title,
            "logged_in": bool(home and not login_required),
            "login_required": login_required, "license_warning": license_problem,
            "home": home, "screenshot": png,
            "text_head": re.sub(r"\s+", " ", text)[:1200]}


async def dom_snapshot() -> dict[str, Any]:
    page, err = await _ensure_page()
    if err:
        return err
    return await core._snap(page)


async def goto_path(path: str, wait: float = 3.0) -> dict[str, Any]:
    page, err = await _ensure_page()
    if err:
        return err
    url = path if str(path).startswith("http") else POWERAPPS_URL + str(path)
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"导航失败: {exc}",
                "url": getattr(page, "url", "")}
    await asyncio.sleep(max(0.5, wait))
    return await core._snap(page)


async def click_text(text: str, role: str = "", wait: float = 2.0) -> dict[str, Any]:
    page, err = await _ensure_page()
    if err:
        return err
    target = str(text or "").strip()
    if not target:
        return {"ok": False, "error": "text 不能为空"}
    clicked = False
    error = ""
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
    page, err = await _ensure_page()
    if err:
        return err
    target_label = str(label or "").strip()
    if not target_label:
        return {"ok": False, "error": "label 不能为空"}
    candidates = []
    for f in page.frames:
        for factory in (
            lambda: f.get_by_label(target_label),
            lambda: f.get_by_placeholder(target_label),
            lambda: f.get_by_role("textbox", name=re.compile(re.escape(target_label), re.I)),
            lambda: f.locator(f'[aria-label*="{target_label.replace(chr(34), chr(92) + chr(34))}"]'),
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
    page, err = await _ensure_page()
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
    page, err = await _ensure_page()
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
