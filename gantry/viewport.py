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
