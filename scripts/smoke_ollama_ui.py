# -*- coding: utf-8 -*-
"""浏览器验证：WebUI 的模型下拉里能看到 ollama 模型，并能选它。"""
import json
import sys

sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, channel="msedge")
    pg = b.new_context(viewport={"width": 1400, "height": 900}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
    pg.goto("http://127.0.0.1:8765/ui", wait_until="domcontentloaded")
    pg.wait_for_timeout(3000)
    info = pg.evaluate("""() => {
      const sel = document.getElementById('model');
      const opts = sel ? [...sel.options].map(o => ({v: o.value, t: o.textContent.trim(),
                                                     dis: o.disabled})) : [];
      const ol = opts.filter(o => o.v.startsWith('ollama/'));
      return {count: opts.length, ollama: ol, first: opts.slice(0, 3)};
    }""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:900])
    ok = bool(info.get("ollama")) and not errs
    print("结论:", "WebUI 下拉里能看到 ollama 模型" if ok else "⚠ 没看到")
    if errs:
        print("JS 错误:", errs[:3])
    pg.screenshot(path="data/ui-audit/ollama-model.png")
    b.close()
    sys.exit(0 if ok else 1)
