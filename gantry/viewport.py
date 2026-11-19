# -*- coding: utf-8 -*-
"""自动化浏览器窗口尺寸：服务与 scripts/login.py 共用同一套默认值/夹取。"""
from __future__ import annotations

from typing import Any, Mapping

# 最小可用尺寸；高度上限 1080，避免在小屏/远程桌面上超出屏幕。
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768
MIN_WIDTH = 800
MAX_WIDTH = 3200
MIN_HEIGHT = 600
MAX_HEIGHT = 1080


def window_size(cfg: Mapping[str, Any] | None = None) -> dict[str, int]:
    cfg = cfg or {}

    def _num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(cfg.get(key) or default)
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    return {"width": _num("browser_width", DEFAULT_WIDTH, MIN_WIDTH, MAX_WIDTH),
            "height": _num("browser_height", DEFAULT_HEIGHT, MIN_HEIGHT, MAX_HEIGHT)}


async def apply_outer_window_size(page: Any, cfg: Mapping[str, Any] | None = None) -> bool:
    """把**整个浏览器窗口**（含标题栏/地址栏）设成目标尺寸。

    Playwright 的 ``viewport`` 只控制页面内容区；浏览器外框会多出约 16×134px
    （实测 1024×768 内容 → 1040×902 窗口）。用户说“窗口 1024×768”通常指外框，
    所以启动后用 CDP 再校正一次。失败不影响使用（例如 headless）。
    """
    size = window_size(cfg)
    try:
        session = await page.context.new_cdp_session(page)
        try:
            info = await session.send("Browser.getWindowForTarget")
            await session.send("Browser.setWindowBounds", {
                "windowId": info["windowId"],
                "bounds": {**size, "windowState": "normal"},
            })
        finally:
            await session.detach()
        return True
    except Exception:                            # noqa: BLE001
        return False

