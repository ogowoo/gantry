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
import json
import re
import time
from pathlib import Path
from typing import Any

PA_URL = "https://make.powerautomate.com/"
PA_DOMAIN = "make.powerautomate.com"
_LOGIN_MARK = re.compile(r"(login\.microsoftonline\.com|login\.live\.com|/oauth2/|/common/login)", re.I)
_AUTH_COOKIE_MARKS = ("powerautomate.com", "powerplatform.com", "microsoftonline.com",
                      "microsoft.com", "office.com",
                      "teams.cloud.microsoft", "teams.microsoft.com")


def _auth_state_path() -> Path:
    from . import bridge as b
    return Path(b.BASE) / "data" / "pa-auth.json"


def _cookie_allowed(domain: str) -> bool:
    d = str(domain or "").lower()
    return any(m in d for m in _AUTH_COOKIE_MARKS)


async def save_auth_state(ctx: Any) -> str:
    """把 Power Platform 相关 cookie 存到 data/pa-auth.json，供下次启动恢复。

    只保存 automation 相关域，避免把 Copilot/Teams 的其它 cookie 一起落盘。
    """
    try:
        state = await ctx.storage_state()
    except Exception:
        return ""
    cookies = [c for c in (state.get("cookies") or [])
               if _cookie_allowed(str(c.get("domain") or ""))]
    if not cookies:
        return ""
    out = _auth_state_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"cookies": cookies}, ensure_ascii=False), encoding="utf-8")
    return str(out)


async def restore_auth_state(ctx: Any) -> int:
    """启动后把上次保存的 cookie 注入上下文（会话 cookie 也能跨重启）。"""
    p = _auth_state_path()
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or []
        if cookies:
            await ctx.add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


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
    if logged_in:
        try:
            await save_auth_state(b.ctx)
        except Exception:
            pass

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
  const seen = new Set();
  const sel = 'button, a, input, select, textarea, [role=button], [role=tab], [role=menuitem], [role=link], [role=dialog]';
  const push = (el) => {
    if (!el || out.length >= 500) return;
    if (seen.has(el)) return;
    seen.add(el);
    const r = el.getBoundingClientRect();
    let st = null;
    try { st = getComputedStyle(el); } catch (e) {}
    if (r.width <= 0 || r.height <= 0) return;
    if (st && (st.visibility === 'hidden' || st.display === 'none')) return;
    out.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      text: ((el.innerText || el.value || '') + '').trim().replace(/\\s+/g, ' ').slice(0, 100),
      aria: el.getAttribute('aria-label') || '',
      role: el.getAttribute('role') || '',
      name: el.getAttribute('name') || '',
      placeholder: el.getAttribute('placeholder') || '',
      href: el.getAttribute('href') || '',
      options: el.tagName === 'SELECT'
        ? [...el.options].map(o => ({ text: (o.text || '').trim(), value: o.value || '' }))
        : []
    });
  };
  const walk = (root) => {
    let els = [];
    try { els = [...root.querySelectorAll('*')]; } catch (e) { return; }
    for (const el of els) {
      try {
        if (el.matches && el.matches(sel)) push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      } catch (e) {}
    }
  };
  walk(document);
  return out;
}
"""


async def _collect_items(page: Any, errors: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """汇总主文档 + 所有 iframe（含跨域 iframe）的可见控件。"""
    items: list[dict[str, Any]] = []
    frames = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = [page]
    for f in frames:
        try:
            part = await f.evaluate(_SNAPSHOT_JS)
        except Exception as exc:
            if errors is not None:
                errors.append({"frame": str(f.url)[:200], "error": str(exc)[:300]})
            continue
        for it in part or []:
            if isinstance(it, dict):
                it["frame"] = f.url
                items.append(it)
    return items


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
        errors: list[dict[str, Any]] = []
        items = await _collect_items(page, errors)
        frames = [f.url for f in page.frames]
    except Exception as exc:
        return {"ok": False, "error": f"读取页面失败: {exc}"}
    return {"ok": True, "url": url, "title": title, "frames": frames,
            "frame_errors": errors, "items": items}


async def goto_path(path: str, wait: float = 3.0) -> dict[str, Any]:
    """在 maker 门户内导航到某个相对路径（诊断/导入流程用）。"""
    from . import bridge as b

    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动"}
    page = await _find_or_open(b.ctx)
    url = path if str(path).startswith("http") else ("https://make.powerautomate.com" + str(path))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    except Exception as exc:
        return {"ok": False, "error": f"导航失败: {exc}", "url": getattr(page, "url", "")}
    await asyncio.sleep(max(0.5, wait))
    return await dom_snapshot()


async def click_text(text: str, role: str = "", wait: float = 2.0) -> dict[str, Any]:
    """按可见文本/角色点击（诊断与导入流程用）；点完返回 DOM 快照。"""
    from . import bridge as b

    if b.ctx is None:
        return {"ok": False, "error": "浏览器未启动"}
    page = await _find_or_open(b.ctx)
    target = str(text or "").strip()
    if not target:
        return {"ok": False, "error": "text 不能为空"}
    clicked = False
    error = ""
    try:
        locs: list[Any] = []
        frames = []
        try:
            frames = list(page.frames)
        except Exception:
            frames = [page]
        pat = re.compile(re.escape(target), re.I)
        for f in frames:
            if role:
                locs.append(f.get_by_role(role, name=pat))
            locs.append(f.get_by_text(pat))
        for loc in locs:
            try:
                if await loc.count():
                    await loc.first.click(timeout=8000)
                    clicked = True
                    break
            except Exception as exc:
                error = str(exc)
    except Exception as exc:
        error = str(exc)
    if not clicked and not error:
        try:
            clicked = bool(await page.evaluate(
                """(label) => {
                  const all = [...document.querySelectorAll('button,a,[role=menuitem],[role=button]')];
                  const el = all.find(e => ((e.innerText || '') + ' ' + (e.getAttribute('aria-label') || '')).trim().includes(label));
                  if (el) { el.click(); return true; }
                  return false;
                }""", target))
        except Exception as exc:
            error = str(exc)
    await asyncio.sleep(max(0.5, wait))
    snap = await dom_snapshot()
    snap["clicked"] = clicked
    if error:
        snap["click_error"] = error
    return snap


# ── 导入流程 ─────────────────────────────────────────────────────
_LEGACY_TEXTS = (
    "Import Package (Legacy)", "Import package (legacy)", "Import Package",
    "导入包（旧版）", "导入包(旧版)", "导入包",
)
_IMPORT_TEXTS = ("Import", "导入")
_NEXT_TEXTS = ("Upload", "Continue", "Next", "上传", "继续", "下一步")
_CONFIRM_TEXTS = ("Import", "导入")
_CREATE_NEW_TEXTS = ("Create as new", "创建为新流", "创建为新", "新建")


async def _click_any(page: Any, locators: list[Any], timeout: int = 8000) -> bool:
    for loc in locators:
        try:
            if await loc.count():
                await loc.first.click(timeout=timeout)
                return True
        except Exception:
            continue
    return False


async def _click_texts(page: Any, texts: tuple[str, ...], role: str = "") -> bool:
    locs: list[Any] = []
    frames = []
    try:
        frames = list(page.frames)
    except Exception:
        frames = [page]
    for f in frames:
        for t in texts:
            if role:
                locs.append(f.get_by_role(role, name=re.compile(re.escape(t), re.I)))
            locs.append(f.get_by_text(re.compile(re.escape(t), re.I)))
    return await _click_any(page, locs)


async def _snap(page: Any, **extra: Any) -> dict[str, Any]:
    try:
        errors: list[dict[str, Any]] = []
        items = await _collect_items(page, errors)
        title = await page.title()
        url = page.url
        frames = [f.url for f in page.frames]
    except Exception as exc:
        return {"ok": False, "error": f"读取页面失败: {exc}"}
    out: dict[str, Any] = {"ok": True, "url": url, "title": title,
                           "frames": frames, "frame_errors": errors, "items": items}
    out.update(extra)
    return out


async def _ensure_page() -> tuple[Any | None, dict[str, Any] | None]:
    from . import bridge as b
    raw = getattr(b, "ENSURE_BROWSER_RAW", None)
    if callable(raw):
        await raw()
    if b.ctx is None:
        return None, {"ok": False, "error": "浏览器未启动"}
    return await _find_or_open(b.ctx), None


def _env_from_url(url: str) -> str:
    m = re.search(r"/environments/([^/]+)/", str(url or ""))
    return m.group(1) if m else ""


async def select_option(label: str = "", value: str = "", index: int = 0) -> dict[str, Any]:
    """在所有 frame 里找第 index 个 <select> 并选择 option（按 label 或 value）。"""
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
                return await _snap(page, selected=False, error="label/value 至少给一个")
            await asyncio.sleep(1)
            return await _snap(page, selected=True)
        except Exception as exc:
            last = str(exc)
    return await _snap(page, selected=False, error=last or "没有找到 select")


async def html_snippet(needle: str, radius: int = 1500) -> dict[str, Any]:
    """返回包含 needle 的 frame HTML 片段（只读诊断）。"""
    page, err = await _ensure_page()
    if err:
        return err
    key = str(needle or "")
    if not key:
        return {"ok": False, "error": "needle 不能为空"}
    for f in page.frames:
        try:
            html = await f.content()
        except Exception:
            continue
        idx = html.find(key)
        if idx >= 0:
            lo = max(0, idx - max(100, int(radius)))
            hi = min(len(html), idx + max(100, int(radius)))
            return {"ok": True, "url": str(f.url)[:300], "html": html[lo:hi]}
    return {"ok": False, "error": f"没有 frame 包含 {key}"}


async def select_connection(label: str) -> dict[str, Any]:
    """在“导入设置”对话框里按连接显示名选中一行（radio/row）。"""
    page, err = await _ensure_page()
    if err:
        return err
    key = str(label or "").strip()
    if not key:
        return {"ok": False, "error": "label 不能为空"}
    for f in page.frames:
        # 优先用真实鼠标点击 pa-list-row（Angular ng-click 对合成事件不一定响应）
        try:
            loc = f.locator("pa-list-row").filter(has_text=key)
            if await loc.count():
                await loc.first.click(timeout=8000)
                await asyncio.sleep(1)
                return await _snap(page, selected=True)
        except Exception:
            pass
        try:
            clicked = await f.evaluate(
                """(label) => {
                  const rows = [...document.querySelectorAll('pa-list-row, tr, [role=row], .content-row')];
                  let hit = null;
                  for (const row of rows) {
                    const type = row.querySelector('.type');
                    const txt = type ? (type.innerText || '').trim() : (row.innerText || '').trim();
                    if (txt.includes(label)) { hit = row; break; }
                  }
                  if (!hit) return false;
                  const target = hit.querySelector('.type') || hit.querySelector('.pa-row-clickable') || hit;
                  target.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, view: window}));
                  return true;
                }""", key)
        except Exception:
            continue
        if clicked:
            await asyncio.sleep(1)
            return await _snap(page, selected=True)
    return await _snap(page, selected=False, error=f"没有找到含 {key} 的连接行")


async def frames_debug() -> dict[str, Any]:
    """逐个 frame 返回标题/HTML 长度/正文开头，用于定位跨域 iframe。"""
    page, err = await _ensure_page()
    if err:
        return err
    out = []
    for f in page.frames:
        item: dict[str, Any] = {"url": str(f.url)[:300]}
        try:
            item["title"] = await f.title()
        except Exception as exc:
            item["title_error"] = str(exc)[:200]
        try:
            html = await f.content()
            item["html_len"] = len(html)
            item["html_head"] = html[:300]
        except Exception as exc:
            item["content_error"] = str(exc)[:200]
        try:
            text = await f.evaluate("() => document.body ? document.body.innerText : ''")
            item["text"] = str(text or "")[:1000]
        except Exception as exc:
            item["text_error"] = str(exc)[:200]
        out.append(item)
    return {"ok": True, "frames": out}


async def import_open() -> dict[str, Any]:
    """打开 My flows → Import → Import Package (Legacy)，返回当前 DOM。"""
    page, err = await _ensure_page()
    if err:
        return err
    await page.bring_to_front()
    try:
        env = _env_from_url(page.url)
        target = (f"https://make.powerautomate.com/environments/{env}/flows"
                  if env else PA_URL)
        await _goto(page, target)
        await asyncio.sleep(3)
    except Exception as exc:
        return {"ok": False, "error": f"打开 My flows 失败: {exc}"}

    import_clicked = await _click_texts(page, _IMPORT_TEXTS, role="menuitem")
    if not import_clicked:
        import_clicked = await _click_texts(page, _IMPORT_TEXTS)
    await asyncio.sleep(2)
    legacy_clicked = await _click_texts(page, _LEGACY_TEXTS)
    await asyncio.sleep(2)
    # 新版导入页把真正的控件放在 make.powerapps.com 的 widget iframe 里，
    # 首屏脚本加载需要几秒；轮询到 Upload/Import 或 file input 出现为止。
    deadline = time.time() + 40
    while time.time() < deadline:
        items = await _collect_items(page)
        widget = [i for i in items if "powerapps.com" in str(i.get("frame") or "")]
        if any(str(i.get("type") or "") == "file" for i in items) or any(
                str(i.get("text") or "").strip() in ("Upload", "Import", "上传", "导入")
                for i in widget):
            break
        await asyncio.sleep(2)
    return await _snap(page, clicked_import=import_clicked, clicked_legacy=legacy_clicked)


async def import_upload(zip_path: str) -> dict[str, Any]:
    """在当前导入页上传 zip（只上传，不点最终导入）。"""
    page, err = await _ensure_page()
    if err:
        return err
    p = Path(zip_path)
    if not p.is_file():
        return {"ok": False, "error": f"文件不存在: {zip_path}"}
    file_inputs = []
    deadline = time.time() + 25
    while time.time() < deadline:
        file_inputs = []
        frames = []
        try:
            frames = list(page.frames)
        except Exception:
            frames = [page]
        for f in frames:
            try:
                loc = f.locator('input[type="file"]')
                if await loc.count():
                    file_inputs.append(loc)
            except Exception:
                continue
        if file_inputs:
            break
        await asyncio.sleep(1)
    if not file_inputs:
        # 有些导入页不暴露 input[type=file]，点 Upload 会弹原生文件选择器；
        # 用 Playwright 的 filechooser 事件直接塞文件。
        try:
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                await _click_texts(page, ("Upload", "上传"), role="button")
            chooser = await fc_info.value
            await chooser.set_files(str(p))
            await asyncio.sleep(3)
            next_clicked = await _click_texts(page, _NEXT_TEXTS)
            await asyncio.sleep(3)
            return await _snap(page, uploaded=True, next_clicked=next_clicked,
                               via="filechooser")
        except Exception as exc:
            return await _snap(page, uploaded=False,
                               error=f"没有 file input，filechooser 也失败: {exc}")
    try:
        await file_inputs[0].first.set_input_files(str(p))
    except Exception as exc:
        return await _snap(page, uploaded=False, error=f"set_input_files 失败: {exc}")
    await asyncio.sleep(3)
    next_clicked = await _click_texts(page, _NEXT_TEXTS)
    await asyncio.sleep(3)
    return await _snap(page, uploaded=True, next_clicked=next_clicked)


async def import_confirm(create_new: bool = True) -> dict[str, Any]:
    """在导入复核页选择“创建为新流”并点击导入。"""
    page, err = await _ensure_page()
    if err:
        return err
    if create_new:
        await _click_texts(page, _CREATE_NEW_TEXTS)
        await asyncio.sleep(1)
    clicked = await _click_texts(page, _CONFIRM_TEXTS)
    await asyncio.sleep(5)
    text = await _body_text(page, 8000)
    lower = text.lower()
    ok = any(k in lower for k in ("imported", "success", "ready", "已完成", "导入成功", "创建成功"))
    return await _snap(page, confirm_clicked=clicked, success=ok)


async def import_run(zip_path: str, dry_run: bool = True) -> dict[str, Any]:
    """完整导入：打开 → 上传 →（dry_run 停止）→ 选择新建 → 确认。"""
    steps: list[dict[str, Any]] = []
    opened = await import_open()
    steps.append({"step": "open", "ok": opened.get("ok"), "url": opened.get("url"),
                  "clicked_import": opened.get("clicked_import"),
                  "clicked_legacy": opened.get("clicked_legacy"),
                  "file_input": any(i.get("tag") == "input" and i.get("type") == "file"
                                    for i in (opened.get("items") or []))})
    if not opened.get("ok"):
        return {"ok": False, "steps": steps, "detail": opened}
    uploaded = await import_upload(zip_path)
    steps.append({"step": "upload", "ok": uploaded.get("ok"), "uploaded": uploaded.get("uploaded"),
                  "error": uploaded.get("error")})
    if not uploaded.get("uploaded"):
        return {"ok": False, "steps": steps, "detail": uploaded}
    if dry_run:
        return {"ok": True, "dry_run": True, "steps": steps, "detail": uploaded}
    confirmed = await import_confirm()
    steps.append({"step": "confirm", "ok": confirmed.get("ok"),
                  "success": confirmed.get("success"),
                  "confirm_clicked": confirmed.get("confirm_clicked")})
    return {"ok": bool(confirmed.get("success")), "dry_run": False,
            "steps": steps, "detail": confirmed}
