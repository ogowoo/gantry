"""visual-design 的固定渲染器：Playwright bundled Chromium + Pillow。

设计边界：
- 不启动 shell、不连接 Copilot Edge；
- 每次渲染使用独立 Chromium context，结束后关闭；
- 页面禁用 JavaScript，网络请求全部阻断；
- 只允许项目内 source/assets 的 file 资源；
- 固定画布尺寸，PNG 像素尺寸与 design spec 一致。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

try:
    from PIL import Image
except Exception:  # pragma: no cover - plugin returns a clear error
    Image = None  # type: ignore[assignment]


class RenderError(RuntimeError):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.hint = hint
        super().__init__(message)

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self),
                **({"hint": self.hint} if self.hint else {})}


_RENDER_LOCK = asyncio.Semaphore(1)


def _configured_channel() -> str:
    """配置里的系统浏览器通道（如 msedge / chrome），取不到就返回空。

    为什么需要：`p.chromium.launch()` 默认用 Playwright 自带的 Chromium。没跑
    `playwright install chromium` 的机器（例如只靠系统 Edge 跑自动化的 Windows
    用户）会直接 `Executable doesn't exist`。本服务本来就要求装 Edge/Chrome，
    所以自带 Chromium 缺失时退回配置通道即可，不必额外下 130 MB。
    """
    try:
        from gantry import bridge as _b

        ch = str(_b.CFG.get("browser_channel") or "").strip()
        return ch
    except Exception:                            # noqa: BLE001 - 单独加载时没有 bridge
        return ""


async def _launch_browser(p, launch_kwargs: dict):
    """先试自带 Chromium，失败再退回系统浏览器通道（Edge/Chrome）。"""
    first_exc: Exception | None = None
    try:
        return await p.chromium.launch(**launch_kwargs)
    except Exception as exc:                     # noqa: BLE001
        first_exc = exc
        if "Timeout" in str(exc) or exc.__class__.__name__.endswith("TimeoutError"):
            raise
    channel = _configured_channel()
    if channel:
        try:
            return await p.chromium.launch(channel=channel, **launch_kwargs)
        except Exception as exc:                 # noqa: BLE001
            if "Timeout" in str(exc) or exc.__class__.__name__.endswith("TimeoutError"):
                raise
            raise RenderError(
                "BROWSER_MISSING",
                f"浏览器启动失败：自带 Chromium 与 {channel} 都不可用"
                f"（Chromium: {first_exc}；{channel}: {exc}）",
                "运行 python install.py 安装 Playwright Chromium，或确认系统已安装 Edge/Chrome",
            ) from exc
    raise RenderError("BROWSER_MISSING", f"Chromium 启动失败: {first_exc}",
                      "请运行 python install.py 安装 Playwright Chromium") from first_exc


def _require_pillow() -> None:
    if Image is None:
        raise RenderError("DEPENDENCY_MISSING",
                          "缺少 Pillow，无法检查/转换图片",
                          "请安装 plugins/visual-design/requirements.txt")


def inspect_png(path: Path) -> dict[str, Any]:
    _require_pillow()
    try:
        with Image.open(path) as im:
            return {"path": str(path), "format": im.format, "width": im.width,
                    "height": im.height, "mode": im.mode, "bytes": path.stat().st_size}
    except Exception as exc:
        raise RenderError("INSPECT_FAILED", f"无法读取图片: {exc}") from exc


def make_preview(src: Path, dst: Path, max_side: int) -> dict[str, Any]:
    _require_pillow()
    try:
        with Image.open(src) as im:
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(dst, format="PNG", optimize=True)
            return {"path": str(dst), "width": im.width, "height": im.height,
                    "bytes": dst.stat().st_size}
    except Exception as exc:
        raise RenderError("PREVIEW_FAILED", f"生成预览失败: {exc}") from exc


def convert_jpg(src: Path, dst: Path, quality: int = 90) -> dict[str, Any]:
    _require_pillow()
    try:
        with Image.open(src) as im:
            if im.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
            im.save(dst, format="JPEG", quality=quality, optimize=True)
            return {"path": str(dst), "format": "JPEG", "width": im.width,
                    "height": im.height, "bytes": dst.stat().st_size}
    except Exception as exc:
        raise RenderError("EXPORT_FAILED", f"JPG 导出失败: {exc}") from exc


def _allow_file(url: str, project: Path) -> bool:
    """只允许项目 source/assets 内的本地文件。"""
    allowed = []
    for root in (project / "source", project / "assets"):
        try:
            real = root.resolve()
            if real == project or project in real.parents:
                allowed.append(real)
        except Exception:
            continue
    try:
        p = Path(url2pathname(unquote(urlparse(url).path))).resolve()
    except Exception:
        return False
    return any(p == root or root in p.parents for root in allowed)


async def _render_locked(project: Path, target: str, width: int, height: int,
                         timeout: float, out_path: Path) -> None:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        raise RenderError("DEPENDENCY_MISSING", f"缺少 Playwright: {exc}",
                          "请运行 python install.py") from exc

    source = project / "source" / ("index.html" if target == "html" else "index.svg")
    if not source.is_file():
        raise RenderError("SOURCE_MISSING", f"源文件不存在: {source.name}")
    if source.is_symlink():
        raise RenderError("PATH_ESCAPE", "源文件不能是符号链接")
    try:
        source.resolve().relative_to(project)
    except ValueError as exc:
        raise RenderError("PATH_ESCAPE", "源文件路径越界") from exc
    if source.stat().st_size > 2_000_000:
        raise RenderError("SOURCE_TOO_LARGE", "源文件超过 2 MB")

    fd, tmp_name = tempfile.mkstemp(prefix=".visual-", suffix=".png",
                                    dir=str(out_path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    browser = None
    try:
        async with async_playwright() as p:
            try:
                browser = await _launch_browser(p, dict(
                    headless=True,
                    timeout=max(1, int(timeout * 1000)),
                    args=[
                        "--disable-background-networking", "--disable-sync",
                        "--disable-extensions", "--no-first-run",
                    ],
                ))
            except RenderError:
                raise
            except Exception as exc:
                if "Timeout" in str(exc) or exc.__class__.__name__.endswith("TimeoutError"):
                    raise RenderError("RENDER_TIMEOUT",
                                      f"浏览器启动超时（>{timeout:g}s）") from exc
                raise RenderError("BROWSER_MISSING",
                                  f"Chromium 启动失败: {exc}",
                                  "请运行 python install.py 安装 Playwright Chromium") from exc
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                java_script_enabled=False,
            )
            page = await context.new_page()

            async def _route(route):
                url = route.request.url
                scheme = urlparse(url).scheme.lower()
                if scheme in ("data", "blob", "about"):
                    await route.continue_()
                    return
                if scheme == "file" and _allow_file(url, project):
                    await route.continue_()
                    return
                # 其它 http/https/file 一律阻断，避免读本地敏感文件或外网。
                await route.abort()

            await page.route("**/*", _route)
            await page.goto(source.as_uri(), wait_until="load",
                            timeout=max(1, int(timeout * 1000)))
            # 给字体/布局一小段稳定时间，但不能超过总超时。
            await page.wait_for_timeout(min(250, max(0, int(timeout * 1000))))
            await page.screenshot(path=str(tmp), full_page=False,
                                  timeout=max(1, int(timeout * 1000)))
            await context.close()
    except RenderError:
        raise
    except asyncio.TimeoutError as exc:
        raise RenderError("RENDER_TIMEOUT", f"渲染超时（>{timeout:g}s）") from exc
    except Exception as exc:
        if exc.__class__.__name__.endswith("TimeoutError") or "Timeout" in str(exc):
            raise RenderError("RENDER_TIMEOUT", f"渲染超时（>{timeout:g}s）") from exc
        raise RenderError("RENDER_FAILED", f"渲染失败: {exc}") from exc
    finally:
        try:
            if browser is not None:
                await browser.close()
        except Exception:
            pass
    try:
        info = inspect_png(tmp)
        if (info["width"], info["height"]) != (width, height):
            raise RenderError(
                "SIZE_MISMATCH",
                f"渲染尺寸 {info['width']}x{info['height']} 与要求 {width}x{height} 不一致")
        os.replace(tmp, out_path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


async def render(project: Path, target: str, width: int, height: int,
                 timeout: float, out_path: Path) -> dict[str, Any]:
    """受控渲染；同一进程内最多一个 Chromium 渲染。"""
    if target not in ("html", "svg"):
        raise RenderError("INVALID_TARGET", "target 必须是 html 或 svg")
    async with _RENDER_LOCK:
        await _render_locked(project, target, width, height, timeout, out_path)
    if out_path.stat().st_size > 25_000_000:
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RenderError("OUTPUT_TOO_LARGE", "渲染输出超过 25 MB")
    return inspect_png(out_path)
