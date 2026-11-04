# -*- coding: utf-8 -*-
"""验证知识库管理页：打开弹窗 → 出现统计/文档列表 → 检索有结果 → 关闭。

用 Playwright 真开一次 /ui/（和 ui_smoke 同一套启动方式），抓控制台错误。
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))


def run(url: str = "http://127.0.0.1:8765/ui/") -> int:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = None
        for kwargs in ({"channel": "msedge"}, {}, {"channel": "chrome"}):
            try:
                browser = p.chromium.launch(headless=True, **kwargs)
                break
            except Exception:                    # noqa: BLE001
                continue
        if browser is None:
            print("无法启动浏览器")
            return 2
        page = browser.new_page(viewport={"width": 1440, "height": 980})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        # 1) 打开知识库弹窗（window.openKb 是动态 import 的包装，等一下）
        page.evaluate("openKb()")
        page.wait_for_timeout(2500)
        if not page.evaluate("document.getElementById('kbMask').classList.contains('open')"):
            problems.append("kbMask 没有打开")
        stats = page.evaluate("(document.getElementById('kbStats')||{}).textContent||''")
        if "文档" not in stats:
            problems.append(f"统计没渲染: {stats[:60]!r}")
        docs = page.evaluate("(document.getElementById('kbDocs')||{}).innerText||''")
        if "imported" not in docs and "还没有文档" not in docs:
            problems.append(f"文档列表异常: {docs[:80]!r}")

        # 2) 检索一次
        page.fill("#kbQ", "打印机脱机")
        page.evaluate("window.kbSearch()")
        page.wait_for_timeout(15000)
        hits = page.evaluate("(document.getElementById('kbHits')||{}).innerText||''")
        if not hits:
            hits = page.evaluate("(document.getElementById('kbHits')||{}).innerHTML||''")[:200]
        if "后端" not in hits and "没有命中" not in hits:
            problems.append(f"检索结果没渲染: {hits[:80]!r}")

        # 3) 真上传一个 docx（FileReader → base64 → POST api/import）
        import io
        import tempfile

        from docx import Document

        doc = Document()
        doc.add_heading("上传测试文档", level=1)
        doc.add_paragraph("这条是通过管理页上传的，用来验证文件导入链路。")
        tmpdir = Path(tempfile.mkdtemp(prefix="kb_upload_"))
        sample = tmpdir / "upload-check.docx"
        buf = io.BytesIO()
        doc.save(buf)
        sample.write_bytes(buf.getvalue())
        try:
            page.set_input_files("#kbFile", str(sample))
            page.wait_for_timeout(12000)
        finally:
            pass
        docs2 = page.evaluate("(document.getElementById('kbDocs')||{}).innerText||''")
        if "upload-check" not in docs2:
            problems.append(f"上传后列表里没有 upload-check: {docs2[:120]!r}")

        # 4) 关闭
        page.evaluate("closeKb()")
        page.wait_for_timeout(400)
        if page.evaluate("document.getElementById('kbMask').classList.contains('open')"):
            problems.append("kbMask 关不上")

        if errors:
            problems.append("页面异常: " + " | ".join(errors[:3]))
        browser.close()

    print("统计:", stats[:110])
    print("文档列表前 80:", docs[:80].replace("\n", " / "))
    print("检索结果前 120:", hits[:120].replace("\n", " / "))
    if problems:
        print("✗ 知识库页检查失败：")
        for x in problems:
            print("  -", x)
        return 1
    print("✓ 知识库管理页通过（打开/统计/列表/检索/关闭）")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
