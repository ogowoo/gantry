"""downloads.py — Copilot 回答区下载：候选链接解析、href 落盘、调试快照、copilot_file（生成文件并下载进工作区）

从 bridge.py 拆出（第四批解耦）。约定：模块级可变状态与配置仍在 bridge.py，
这里经 ``b.xxx`` 读写；本模块内部互相调用保持直接调用。本模块不定义 FastAPI 端点。
"""
from __future__ import annotations

import asyncio, base64, collections, contextlib, json, os, re, shutil, tempfile, time, uuid, zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

# bridge 在文件尾导入（bridge.py 尾部反向导入本模块的符号；顶部导入会在
# 直接 import gantry.downloads 时触发循环导入）。所有 b.xxx 均为运行时访问。


DOWNLOAD_HINT_RE = re.compile(r"download|下载|保存文件|save file", re.I)


async def _answer_area_html() -> str:
    """取最近一条回答所在区域的外层 HTML，用于提取下载入口与失败诊断。"""
    try:
        nodes = b.page.locator('[data-content="ai-message"], [class*="ai-message" i], '
                             '[class*="assistant" i], article')
        count = await nodes.count()
        if count:
            return await nodes.nth(count - 1).evaluate("el => el.outerHTML")
    except Exception:
        pass
    try:
        return await b.page.locator("body").evaluate("el => el.outerHTML")
    except Exception:
        return ""


async def _download_candidates():
    """当前页面里可能的下载入口，新的排在前面。

    实测形态：a[download]、blob:/data: 链接、文件卡片里的链接、带
    "下载/Download" 文案或 aria-label 的按钮。
    """
    candidates = []
    selectors = [
        'a[download]',
        'a[href^="blob:"]',
        'a[href^="data:"]',
        '[role="link"][href^="blob:"]',
        '[class*="file" i] a[href]',
        'button[aria-label*="Download" i]',
        'button[aria-label*="下载"]',
        'a[aria-label*="Download" i]',
        'a[aria-label*="下载"]',
        '[role="button"][aria-label*="Download" i]',
        '[role="button"][aria-label*="下载"]',
    ]
    for sel in selectors:
        loc = b.page.locator(sel)
        try:
            for i in range(await loc.count()):
                el = loc.nth(i)
                try:
                    if not await el.is_visible():
                        continue
                    href = await el.get_attribute("href") or ""
                    # 排除误报：会话/引用链接（实测 [class*=file i] a[href] 会匹配到
                    # 引用角标，把 /chat/conversation/... 当成下载入口；若被选中会把
                    # 对话页 HTML 写进目标文件，比报错更糟）。
                    if "/chat/conversation" in href or href in ("#", "/"):
                        continue
                    is_dl_href = href.startswith(("blob:", "data:"))
                    dl_attr = await el.get_attribute("download")
                    if not is_dl_href and not dl_attr and sel.startswith('[class*="file" i]'):
                        continue          # 文件卡片里的普通链接：没有下载语义，跳过
                    name = ((dl_attr or "")
                            + " " + ((await el.inner_text()) or "")[:60]
                            + " " + ((await el.get_attribute("aria-label")) or ""))
                    candidates.append({"el": el, "href": href, "text": name.strip()})
                except Exception:
                    continue
        except Exception:
            continue
    return candidates


def _resolve_href(href: str) -> str:
    """把下载候选的 href 规范成可 fetch 的绝对 URL；不可用的返回空串。

    实测故障：`[class*="file" i] a[href]` 会匹配到 `<a href="#">` / 相对路径，
    直接 `ctx.request.get()` 会抛 "Invalid URL"（错误信息完全看不出是这个原因）。
    """
    href = (href or "").strip()
    if not href or href in ("#", "/"):
        return ""
    if href.startswith(("blob:", "data:", "http://", "https://")):
        return href
    # 相对 URL：用当前页面地址补全
    try:
        from urllib.parse import urljoin
        base = b.page.url or ""
        if base.startswith(("http://", "https://")):
            joined = urljoin(base, href)
            return joined if joined.startswith(("http://", "https://")) else ""
    except Exception:
        pass
    return ""


def pathlib_basename(text: str) -> str:
    """从文案里取出文件名（去掉路径分隔与多余字符），用于诊断落盘。"""
    import re as _re
    m = _re.search(r"([\w\-\u4e00-\u9fff]+\.[A-Za-z0-9]{1,8})", str(text or ""))
    return m.group(1) if m else ""


async def debug_download_now(stem_hint: str = "") -> dict:
    """诊断：把当前页面第一个 blob:/data: 下载候选立刻落盘到 data/debug-download/。

    用于验证"页面里明明有下载入口、工具却拿不到"的问题；不经过 Agent 回合。
    """
    out = {"url": getattr(b.page, "url", "") if b.page else "", "saved": None, "candidates": []}
    cands = await _download_candidates()
    out["candidates"] = [{"href": c.get("href") or "", "text": (c.get("text") or "")[:100]}
                         for c in cands]
    dl = [c for c in cands if (c.get("href") or "").startswith(("blob:", "data:"))]
    pick = None
    for c in dl:
        if stem_hint and stem_hint.lower() in (c.get("text") or "").lower():
            pick = c
            break
    pick = pick or (dl[0] if dl else None)
    if pick is None:
        out["error"] = "页面里没有 blob:/data: 下载候选"
        return out
    target = b.BASE / "data" / "debug-download" / (pathlib_basename(pick.get("text")) or "download.bin")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        size = await _save_from_href(_resolve_href(pick["href"]), target)
        out["saved"] = {"path": str(target), "bytes": size,
                        "head": target.read_text(encoding="utf-8", errors="replace")[:300]}
    except Exception as exc:
        out["error"] = f"下载失败: {exc}"
    return out


async def debug_page_snapshot() -> dict:
    """诊断：当前自动化页面截图（base64 PNG）+ 识别到的下载入口。

    只给排障用（用户手动在自动化窗口操作时，从外部看页面现场）：
    截图看 Copilot 回答里有没有"下载/保存"入口，candidates 看提取到的元素。
    """
    out = {"url": getattr(b.page, "url", "") if b.page else "", "candidates": [], "png": ""}
    try:
        if b.page:
            raw = await b.page.screenshot(full_page=False)
            out["png"] = base64.b64encode(raw).decode()
    except Exception as exc:
        out["error"] = f"screenshot: {exc}"
    try:
        for c in await _download_candidates():
            href = c.get("href") or ""
            out["candidates"].append({"href": href, "resolved": _resolve_href(href),
                                      "text": (c.get("text") or "")[:120]})
    except Exception as exc:
        out["error_candidates"] = str(exc)
    return out


async def _copilot_file_max_bytes() -> int:
    """copilot_file 单次下载上限（默认 512MB，config.json 可用 agent.copilot_file_max_mb 调整）。

    blob:/data: 走页内 fetch + base64，http(s) 走 resp.body()——两条路都是整文件进内存，
    没有 Range/分块。不封顶时一个超大文件直接把服务内存打爆（base64 还要再膨胀 1/3）。
    """
    try:
        mb = int(b.AGENT_CFG.get("copilot_file_max_mb") or 512)
    except Exception:                        # noqa: BLE001
        mb = 512
    return max(1, mb) * 1024 * 1024


async def _save_from_href(href: str, target: Path) -> int:
    """把 blob:/data:/http(s) 内容写入目标文件，返回字节数。

    blob: 走页内 fetch + FileReader（页面上下文，天然带登录态）；
    http(s) 走浏览器上下文的 request（复用 Cookie）。
    """
    _max = await _copilot_file_max_bytes()
    if href.startswith("blob:") or href.startswith("data:"):
        # JS 侧先查 size：超限直接中止，不把 base64 全量传出页面。
        try:
            b64 = await b.page.evaluate(
                """async (args) => {
                    const resp = await fetch(args.url);
                    const buf = await resp.arrayBuffer();
                    if (buf.byteLength > args.max) {
                        throw new Error('__CBP_TOO_LARGE__:' + buf.byteLength);
                    }
                    let bin = '';
                    const bytes = new Uint8Array(buf);
                    const CH = 0x8000;
                    for (let i = 0; i < bytes.length; i += CH) {
                        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
                    }
                    return btoa(bin);
                }""", {"url": href, "max": _max})
        except Exception as exc:
            if "__CBP_TOO_LARGE__" in str(exc):
                raise b.ToolError(f"下载内容超过单次上限 {_max // 1048576}MB"
                                  f"（agent.copilot_file_max_mb 可调，已在页面侧中止）")
            raise
        data = base64.b64decode(b64)
    else:
        resp = await b.ctx.request.get(href, timeout=120000)
        if not resp.ok:
            raise b.ToolError(f"下载失败 HTTP {resp.status}: {href[:120]}")
        # Playwright APIRequestContext 不支持流式读：先按 Content-Length 预检，
        # 长度未知时只能整取 body() 后兜底校验（超大即拒，不落盘）。
        cl = (resp.headers.get("content-length") or "").strip()
        if cl:
            try:
                if int(cl) > _max:
                    raise b.ToolError(f"下载内容 {int(cl) / 1048576:.0f}MB 超过单次上限 "
                                      f"{_max // 1048576}MB（agent.copilot_file_max_mb 可调）")
            except ValueError:
                pass
        data = await resp.body()
    if len(data) > _max:
        raise b.ToolError(f"下载内容 {len(data) / 1048576:.0f}MB 超过单次上限 "
                          f"{_max // 1048576}MB（agent.copilot_file_max_mb 可调）")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return len(data)


async def _copilot_file_timeout_seconds() -> float:
    """copilot_file 生成回合的无进度超时（默认 600s，config.json 可用
    agent.copilot_file_timeout_seconds 调整）。

    为什么单独一档：生成可下载文件前 Copilot 常做联网检索+排版渲染，
    安静期轻松超过普通问答的 reply_timeout_seconds（180s）——实测生成
    一份带检索的 PDF 时 180s 无进度即被杀，文件其实还在生成。
    """
    try:
        sec = float(b.AGENT_CFG.get("copilot_file_timeout_seconds") or 600)
    except Exception:                        # noqa: BLE001
        sec = 600.0
    return max(60.0, sec)


async def _download_chosen(chosen: dict, dest: Path) -> int:
    """把选中的下载入口落到 dest，返回字节数。

    href 可 fetch（blob:/data:/http(s)）直接取；否则是按钮型入口
    （或 href 为 #/相对路径），点击并把浏览器下载事件接住。
    """
    _href = _resolve_href(chosen.get("href") or "")
    if _href:
        return await _save_from_href(_href, dest)
    try:
        async with b.page.expect_download(timeout=120000) as dl_info:
            await chosen["el"].click(timeout=8000)
        download = await dl_info.value
        await download.save_as(str(dest))
        size = dest.stat().st_size
        _max = await _copilot_file_max_bytes()
        if size > _max:
            try:
                dest.unlink(missing_ok=True)   # 超限不留半截文件
            except OSError:
                pass
            raise b.ToolError(f"下载内容 {size / 1048576:.0f}MB 超过单次上限 "
                              f"{_max // 1048576}MB（agent.copilot_file_max_mb 可调）")
        return size
    except b.ToolError:
        raise
    except Exception as exc:
        raise b.ToolError(f"点击下载入口失败: {exc}") from exc


def _sensitive_name(name: str) -> bool:
    """zip 条目名是否属于凭据/密钥类，一律不落盘（宁可少给，不可漏密钥）。"""
    from .sensitive_names import sensitive_name as _sn
    return _sn(name)


def _looks_like_zip(p: Path) -> bool:
    """按魔数判断是否是 zip（PK\x03\x04 本地头 / PK\x05\x06 空包）。"""
    try:
        with open(p, "rb") as fh:
            return fh.read(4) in (b"PK\x03\x04", b"PK\x05\x06")
    except OSError:
        return False


def _extract_copilot_zip(zip_path: Path, target: Path, *, max_bytes: int,
                         snap=None) -> dict:
    """把 Copilot 打包的 zip 安全解到目标路径（扁平解压 + zip-slip 防护）。

    - 条目按 basename 平铺进临时目录再搬移；绝对路径 / .. / 盘符一律拒绝；
    - 同名条目（含 Windows 大小写不敏感）逐个重命名，互不覆盖、不丢失；
    - 目标文件名必须在包内；包内只有一个文件时直接采用它（Copilot 可能改名）；
    - 多余文件统一放进 `<stem>_extras/` 子目录——绝不落工作区根（避免
      sitecustomize.py 之类被本地验证子进程意外 import），也绝不覆盖旧文件；
    - 凭据类文件名（secrets/.env/私钥等）一律跳过不落盘，在 skipped_extras 里报告；
    - snap(rel, dest) 回调在每次写入前调用：extras 也进快照，任务回滚能一起恢复。
    返回 {path, bytes, extracted, extras, skipped_extras}。任何失败都不在
    工作区留下半成品。
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:      # 上下文管理：异常路径也要及时关句柄
            entries = [i for i in zf.infolist() if not i.filename.endswith("/")]
            if not entries:
                raise b.ToolError("Copilot 返回的 zip 是空的；请让它重新打包")
            total = sum(int(i.file_size) for i in entries)
            if total > max_bytes:
                raise b.ToolError(f"zip 解压后 {total / 1048576:.0f}MB 超过单次上限 "
                                  f"{max_bytes // 1048576}MB（agent.copilot_file_max_mb 可调）")
            tmp = Path(tempfile.mkdtemp(prefix="cbfile_"))
            try:
                names: list[str] = []
                tmpfiles: dict[str, Path] = {}
                seen: set[str] = set()             # casefold，Windows 大小写不敏感
                skipped_extras: list[str] = []
                for info in entries:
                    n = info.filename.replace("\\", "/")
                    segs = n.split("/")
                    if n.startswith("/") or ".." in segs or any(":" in s for s in segs):
                        raise b.ToolError(f"zip 内含可疑路径 {info.filename!r}，已拒绝（防路径穿越）")
                    base = os.path.basename(n)
                    if not base:
                        continue
                    if _sensitive_name(base):
                        skipped_extras.append(base)
                        continue
                    if base.casefold() in seen:    # 同名条目：循环加序号直到唯一
                        stem, ext = os.path.splitext(base)
                        k = 2
                        while f"{stem}_{k}{ext}".casefold() in seen:
                            k += 1
                        base = f"{stem}_{k}{ext}"
                    seen.add(base.casefold())
                    try:
                        (tmp / base).write_bytes(zf.read(info))
                    except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
                        raise b.ToolError(f"zip 条目 {info.filename!r} 读取失败（{exc}）；"
                                          f"请让 Copilot 重新打包") from exc
                    names.append(base)
                    tmpfiles[base] = tmp / base
                if not names:
                    raise b.ToolError("zip 里的文件全部被安全规则跳过（凭据类文件不落盘）；"
                                      "请让 Copilot 重新打包或改用分块写入")
                if target.name in names:
                    picked = target.name
                elif len(names) == 1:
                    picked = names[0]              # Copilot 改名：唯一文件直接采用
                else:
                    raise b.ToolError(f"zip 里没有 {target.name}（实际包含：{', '.join(names)}）；"
                                      f"请让 Copilot 用准确的文件名重新打包")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tmpfiles[picked]), str(target))
                extras: list[str] = []
                extras_dir = target.parent / f"{target.stem}_extras"
                for n in names:
                    if n == picked:
                        continue
                    dest = extras_dir / n
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if snap:
                        try:
                            rel = str(dest.relative_to(b.AGENT_TOOLS.root))
                        except Exception:          # noqa: BLE001
                            rel = str(dest)
                        snap(rel, dest)
                    shutil.move(str(tmpfiles[n]), str(dest))
                    extras.append(str(dest))
                size = target.stat().st_size
                return {"path": str(target), "bytes": size, "extracted": names,
                        "extras": extras, "skipped_extras": skipped_extras}
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except (zipfile.BadZipFile, OSError) as exc:
        raise b.ToolError(f"Copilot 返回的不是有效 zip 包（{exc}）；请重试或改用分块写入") from exc


async def _copilot_file(path: str, instruction: str) -> dict:
    """Agent 工具 copilot_file：让 Copilot 生成文件并下载到工作区。

    与 write_text_b64 分块写的根本区别：文件内容不经过聊天消息，因此不受
    Copilot 单条输出上限约束——一个大文件一个回合就能拿到，不再截断。
    代价是内容由 Copilot 侧生成，本地无法逐块校验；写完后仍按普通写入
    走验证闭环（.py/.json/.html 需要 validate_python + run_unittest）。

    zip 传输（2026-11-26 用户实测）：Office/PDF/HTML 这类 Copilot 会渲染
    预览卡片的文件，下载入口常被预览组件劫持——点击只会落下一串无后缀、
    SID 风格命名的内部资源，真正的文件拿不到；zip 不触发预览、下载稳定。
    因此非 zip 目标统一让 Copilot 打包成 zip，本地安全解压落盘。
    """
    if not b.AGENT_CFG.get("allow_copilot_file", True):
        raise b.ToolError("copilot_file 已在 config.json 中禁用（allow_copilot_file=false）")
    instruction = str(instruction or "").strip()
    if not instruction:
        raise b.ToolError("instruction 不能为空")
    target = b.AGENT_TOOLS._path(path)
    try:
        target.relative_to(b.AGENT_TOOLS.root)
    except ValueError as exc:
        raise b.ToolError("copilot_file 只能写入当前工作区") from exc
    filename = target.name
    is_zip_target = filename.lower().endswith(".zip")
    zipname = filename if is_zip_target else (target.stem + ".zip")
    model = b.DEFAULT_MODEL
    if b.current_session():
        st = b.AGENT_SESSIONS.get(b.current_session())
        if st and st.get("model"):
            model = st["model"]
    # 生成可下载文件需要**非 Auto** 模型：实测 Auto 只在聊天里回答、不提供下载入口。
    # 会话模型是 auto 时，改用 agent.copilot_file_model 指定的具体模型（没配则不换，
    # 失败时下文会明确提示原因）。
    _file_model = str(b.AGENT_CFG.get("copilot_file_model") or "").strip()
    _auto_models = ("copilot-auto", "copilot-chat-basic-web")
    if _file_model and model in _auto_models:
        model = _file_model
    if is_zip_target:
        prompt = (f"请把结果生成为一个可下载的文件，文件名：{filename}\n"
                  f"要求：只生成文件并提供下载入口，不要在回答里粘贴文件正文或大段内容。\n"
                  f"内容要求：{instruction}")
    else:
        # 预览卡片会劫持直接下载（Office/PDF/HTML 实测拿不到真文件），
        # 统一让 Copilot 打包 zip——zip 不触发预览，下载稳定（用户验证）。
        prompt = (f"请把结果打包为一个 zip 压缩包并提供下载入口：zip 文件名 {zipname}，"
                  f"里面包含文件 {filename}\n"
                  f"要求：只生成 zip 并提供下载入口，不要在回答里粘贴文件正文或大段内容；"
                  f"zip 里只放最终成品文件，不要附带说明或草稿。\n"
                  f"内容要求：{instruction}")
    # 生成阶段安静期长（检索+渲染），用本工具专属的更长无进度超时，
    # 不要用普通问答的 reply_timeout_seconds（实测 180s 会误杀还在生成的文件）。
    await b.agent_chat_once(prompt, model, False,
                            timeout=await _copilot_file_timeout_seconds())

    # 找下载入口：优先文件名匹配，其次取最新的一个
    candidates = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        candidates = await _download_candidates()
        if candidates:
            break
        await asyncio.sleep(0.5)
    if not candidates:
        dump = await _answer_area_html()
        try:
            (b.BASE / "last_answer.html").write_text(dump[:400000], encoding="utf-8")
        except Exception:
            pass
        _hint = ""
        if model in _auto_models:
            _hint = ("当前模型是 Auto：它通常只在聊天里回答、不生成下载入口。"
                     "请选择具体模型（如 GPT-5.6 Sol Think deeper），"
                     "或配置 agent.copilot_file_model。")
        raise b.ToolError("Copilot 回答里没有找到下载入口（已存 last_answer.html 供排查）；"
                        + _hint
                        + "可改用 write_text_b64/append_text_b64 分块写入")

    # 候选排序（zip 优先）：预览卡片的真文件入口常与 zip 同时出现在页面里，
    # 先试 zip 命中的入口（名字/href 含 .zip），避免把真文件内容当 zip 解压
    # 误报失败；zip 候选用尽后再试其余，最后退化直存（见下）。
    stem = Path(zipname).stem.lower()

    def _zip_hit(c):
        return ".zip" in ((c.get("text") or "") + " " + (c.get("href") or "")).lower()

    def _stem_hit(c):
        return bool(stem) and stem in ((c.get("text") or "") + " " + (c.get("href") or "")).lower()

    _dl = [c for c in candidates if (c.get("href") or "").startswith(("blob:", "data:"))]
    ordered = ([c for c in _dl if _zip_hit(c)]
               + [c for c in _dl if not _zip_hit(c)]
               + [c for c in candidates if c not in _dl])

    # 诊断：把本次识别到的下载入口记下来（href 是否可用、文案是否匹配），
    # 排查"Invalid URL / 下载到 0 字节"时对照真实页面结构。
    try:
        (b.BASE / "data" / "copilot_file_candidates.json").write_text(
            json.dumps([{"href": c.get("href") or "", "resolved": _resolve_href(c.get("href") or ""),
                         "text": c.get("text") or ""} for c in candidates],
                       ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    b.AGENT_TOOLS._snap("copilot_file", path, target)   # 覆盖前存原像，可回滚
    before = b.AGENT_TOOLS._hash(target)

    def _target_event(result="ok"):
        return {"tool": "copilot_file", "path": path, "before_hash": before,
                "after_hash": b.AGENT_TOOLS._hash(target), "result": result}

    if is_zip_target:
        # 目标本身就是 zip：直接落盘，不做解压（zip 下载稳定，无需中转）。
        size = await _download_chosen(ordered[0], target)
        if size <= 0:
            raise b.ToolError("下载到 0 字节，可能不是有效文件；请让 Copilot 重新生成或改用分块写入")
        return {"path": path, "bytes": size, "filename": filename,
                "source": "copilot-download",
                "file_event": _target_event()}

    # 非 zip 目标：逐个候选试到有效 zip；全部失败时若存在与目标文件名匹配的
    # 直接入口，退化回旧行为直接保存（Copilot 可能无视打包要求直接给了文件，
    # 此时直存好过硬失败）。临时目录放系统临时区，失败/中断不留半成品。
    tmpdir = Path(tempfile.mkdtemp(prefix="cbfile_"))
    tmpzip = tmpdir / zipname
    last_err = None
    tried = 0
    out = None
    try:
        for cand in ordered:
            tried += 1
            try:
                zsize = await _download_chosen(cand, tmpzip)
            except b.ToolError as exc:
                last_err = exc
                continue
            if zsize <= 0:
                last_err = b.ToolError("下载到 0 字节")
                continue
            if not _looks_like_zip(tmpzip):
                last_err = b.ToolError("下载内容不是 zip（魔数不符），可能是预览入口")
                try:
                    tmpzip.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            try:
                out = _extract_copilot_zip(
                    tmpzip, target, max_bytes=await _copilot_file_max_bytes(),
                    snap=lambda rel, dest: b.AGENT_TOOLS._snap("copilot_file", rel, dest))
                break
            except b.ToolError as exc:
                last_err = exc
                try:
                    tmpzip.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    if out is None:
        direct = next((c for c in ordered if _stem_hit(c) and not _zip_hit(c)), None)
        if direct is not None:
            try:
                size = await _download_chosen(direct, target)
                if size > 0:
                    return {"path": path, "bytes": size, "filename": filename,
                            "source": "copilot-download-direct",
                            "extracted": [filename], "extras": [],
                            "file_event": _target_event()}
            except b.ToolError as exc:
                last_err = exc
        raise b.ToolError(
            f"Copilot 没有提供可用的 zip 下载入口（{tried} 个候选均失败；"
            f"最后错误：{last_err or '无有效候选'}）；可让 Copilot 重新打包生成，"
            f"或改用 write_text_b64 分块写入")
    # extras 也进变更视图（file_events），且已快照、任务回滚可一并恢复
    extra_events = []
    for ep in out.get("extras") or []:
        try:
            ah = b.AGENT_TOOLS._hash(Path(ep))
            extra_events.append({"tool": "copilot_file",
                                 "path": str(Path(ep).relative_to(b.AGENT_TOOLS.root)),
                                 "before_hash": None, "after_hash": ah, "result": "ok"})
        except Exception:                        # noqa: BLE001
            pass
    return {"path": path, "bytes": out["bytes"], "filename": filename,
            "source": "copilot-download-zip",
            "extracted": out["extracted"],
            "extras": [str(x) for x in out["extras"]],
            "skipped_extras": out.get("skipped_extras") or [],
            "file_event": _target_event(),
            "file_events": extra_events}


from . import bridge as b  # noqa: E402,F401  （晚导入：见文件头注释）
