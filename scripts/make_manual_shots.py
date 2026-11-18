# -*- coding: utf-8 -*-
"""生成使用手册的截图（主界面 / 知识库管理页），放到 webui/img/。

用 Playwright 真开 /ui/，等界面稳定后截图；知识库页需要先 openKb() 再截。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

OUT = Path("webui/img")
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="msedge")
    page = browser.new_page(viewport={"width": 1440, "height": 900},
                            device_scale_factor=1)
    page.goto("http://127.0.0.1:8765/ui/", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    # 主界面（任务区 + 侧栏）
    page.screenshot(path=str(OUT / "quickstart-main.png"), full_page=False)
    print("main:", (OUT / "quickstart-main.png").stat().st_size, "bytes")

    # 知识库管理页
    page.evaluate("window.openKb()")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(OUT / "quickstart-kb.png"), full_page=False)
    print("kb:", (OUT / "quickstart-kb.png").stat().st_size, "bytes")
    page.evaluate("window.closeKb()")

    # 使用手册弹窗
    page.evaluate("openManual()")
    page.wait_for_timeout(1200)
    page.screenshot(path=str(OUT / "quickstart-manual.png"), full_page=False)
    print("manual:", (OUT / "quickstart-manual.png").stat().st_size, "bytes")
    browser.close()
