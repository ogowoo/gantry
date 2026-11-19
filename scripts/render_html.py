"""把本地 HTML 渲染成 PNG（Gantry 海报/封面任务用）。

用法：
  .venv\\Scripts\\python.exe scripts/render_html.py poster.html poster.png 1080 1440
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("png")
    ap.add_argument("width", nargs="?", type=int, default=1080)
    ap.add_argument("height", nargs="?", type=int, default=1440)
    ap.add_argument("--full-page", action="store_true")
    ap.add_argument("--channel", default="msedge")
    args = ap.parse_args()

    html = Path(args.html).resolve()
    out = Path(args.png).resolve()
    if not html.is_file():
        raise SystemExit(f"HTML 不存在: {html}")
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=args.channel, headless=True)
        page = browser.new_page(viewport={"width": args.width, "height": args.height},
                                device_scale_factor=1)
        page.goto(html.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(out), full_page=bool(args.full_page))
        browser.close()

    print(json.dumps({"html": str(html), "png": str(out),
                      "width": args.width, "height": args.height,
                      "bytes": out.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
