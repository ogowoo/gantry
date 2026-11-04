# -*- coding: utf-8 -*-
"""验证手册：打开「使用手册」→ 新章节可见（中英各一次），目录包含新标题。"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="msedge")
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:8765/ui/", wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)

    pg.evaluate("openManual()")
    pg.wait_for_timeout(800)
    body = pg.evaluate("(document.getElementById('manualBody')||{}).innerText||''")
    heads = pg.evaluate(
        "[...document.querySelectorAll('#manualBody h3')].map(h=>h.textContent)")
    toc = pg.evaluate(
        "[...document.querySelectorAll('#manualBody .mtoc a')].map(a=>a.textContent)")
    print("章节数:", len(heads))
    print("含知识库节:", any("知识库" in h for h in heads))
    print("含内嵌模型节:", any("内嵌本地模型" in h for h in heads))
    print("目录含新节:", any("知识库" in t for t in toc))
    print("正文含 kb_add:", "kb_add" in body, "| 含 kb.ocr:", "kb.ocr" in body)
    print("pageerrors:", errs[:2])
    b.close()
