# -*- coding: utf-8 -*-
"""验证手册里的配图真的加载出来了（naturalWidth>0），并统计章节/图注。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="msedge")
    pg = browser.new_page(viewport={"width": 1440, "height": 1000})
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8765/ui/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.evaluate("openManual()")
    pg.wait_for_timeout(2000)
    imgs = pg.evaluate(
        "[...document.querySelectorAll('#manualBody figure img')].map(i=>"
        "({src:i.getAttribute('src'),w:i.naturalWidth,h:i.naturalHeight}))")
    caps = pg.evaluate("document.querySelectorAll('#manualBody figcaption').length")
    heads = pg.evaluate("document.querySelectorAll('#manualBody h3').length")
    print("章节数:", heads, "| 图注:", caps)
    for i in imgs:
        print("  图:", i)
    print("图片都加载成功:", all(i["w"] > 0 for i in imgs) and bool(imgs))
    print("pageerrors:", errs[:2])
    browser.close()
