# -*- coding: utf-8 -*-
"""kb_ingest 测试：各格式提取、URL 抓取（假网络）、落盘与 front-matter、插件导入/删除/收件箱。

docx/xlsx/pptx/pdf 用库现场生成最小文件，不依赖外部样本。
"""
import asyncio
import base64
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import kb_ingest as ing  # noqa: E402
from gantry.kb import KnowledgeBase  # noqa: E402

spec = importlib.util.spec_from_file_location("kb_plugin_imp", ROOT / "plugins" / "kb" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


def make_docx() -> bytes:
    from docx import Document

    d = Document()
    d.add_heading("VPN 使用手册", level=1)
    d.add_heading("常见问题", level=2)
    d.add_paragraph("连不上时先换手机热点试一次。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "症状"
    t.cell(0, 1).text = "处理"
    t.cell(1, 0).text = "无法建立隧道"
    t.cell(1, 1).text = "换端口 443"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "资产"
    ws.append(["主机名", "IP"])
    ws.append(["pc-001", "10.0.0.5"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pptx() -> bytes:
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "网络应急流程"
    slide.placeholders[1].text = "1. 断网先看灯\n2. 换网线"
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def make_pdf() -> bytes:
    """用 fpdf 生成一个带文本层的 PDF。"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "VPN troubleshooting guide")
    pdf.ln(10)
    pdf.cell(0, 10, "Port 443 must be allowed")
    return bytes(pdf.output())


class ExtractTests(unittest.TestCase):
    def test_plain_text(self):
        text = ing.extract_text("a.md", "# 标题\n\n正文".encode())
        self.assertIn("正文", text)

    def test_html_strips_script(self):
        html = ("<html><head><title>网络手册</title><script>alert(1)</script></head>"
                "<body><h2>排查</h2><p>换网线</p><li>看灯</li></body></html>")
        title, body = ing._html_to_text(html)
        self.assertEqual(title, "网络手册")
        self.assertIn("排查", body)
        self.assertIn("- 看灯", body)
        self.assertNotIn("alert", body)

    def test_docx(self):
        text = ing.extract_text("vpn.docx", make_docx())
        self.assertIn("# VPN 使用手册", text)
        self.assertIn("## 常见问题", text)
        self.assertIn("换手机热点", text)
        self.assertIn("换端口 443", text, "表格内容也要进来")

    def test_xlsx(self):
        text = ing.extract_text("assets.xlsx", make_xlsx())
        self.assertIn("## 资产", text)
        self.assertIn("pc-001", text)

    def test_pptx(self):
        text = ing.extract_text("deck.pptx", make_pptx())
        self.assertIn("网络应急流程", text)
        self.assertIn("换网线", text)

    def test_pdf(self):
        text = ing.extract_text("guide.pdf", make_pdf())
        self.assertIn("VPN troubleshooting guide", text)

    def test_unsupported(self):
        with self.assertRaises(ing.IngestError) as ctx:
            ing.extract_text("old.doc", b"x")
        self.assertIn("另存为", str(ctx.exception))

    def test_empty(self):
        with self.assertRaises(ing.IngestError):
            ing.extract_text("a.txt", b"")


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ing_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slug(self):
        self.assertEqual(ing.slug("VPN 使用手册"), "VPN-使用手册")
        self.assertEqual(ing.slug("a/b:c*d"), "a-b-c-d")
        self.assertEqual(ing.slug(""), "document")

    def test_save_and_front_matter(self):
        path = ing.save_document(self.tmp, "vpn.docx", "# 正文", title="VPN 手册",
                                 source="inbox:vpn.docx")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(path.parent.name == "imported")
        self.assertIn("title: VPN 手册", text)
        self.assertIn("category: imported", text)
        self.assertIn("source: inbox:vpn.docx", text)
        self.assertIn("# VPN 手册", text)

    def test_same_title_overwrites(self):
        a = ing.save_document(self.tmp, "x", "一", title="同名")
        b = ing.save_document(self.tmp, "x", "二", title="同名")
        self.assertEqual(a, b, "同标题应覆盖，避免重复导入产生多份")
        self.assertIn("二", b.read_text(encoding="utf-8"))

    def test_fetch_url_rejects_bad_scheme(self):
        with self.assertRaises(ing.IngestError):
            ing.fetch_url("ftp://example.com")


class FakeResponse:
    def __init__(self, body: bytes, ctype="text/html; charset=utf-8"):
        self._body = body
        self.headers = {"content-type": ctype}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class UrlTests(unittest.TestCase):
    def test_fetch_url_parses_html(self):
        html = ("<html><head><title>IT 服务台</title></head><body>"
                "<h1>报修</h1><p>拨打 8000</p></body></html>").encode("utf-8")
        orig = ing.urllib.request.urlopen
        ing.urllib.request.urlopen = lambda *a, **k: FakeResponse(html)
        try:
            title, text = ing.fetch_url("https://example.com/it")
        finally:
            ing.urllib.request.urlopen = orig
        self.assertEqual(title, "IT 服务台")
        self.assertIn("拨打 8000", text)


class FakeCtx:
    def __init__(self, **cfg):
        self.config = dict(cfg)
        self.state = {}
        self.logs = []
        self.tools = {}
        self.apis = {}
        self.panels = []
        self._sem = None

    def log(self, m, *a, **k):
        self.logs.append(str(m))

    def save_state(self, **kw):
        self.state.update(kw)

    def register_tool(self, n, f, d, args=None, **k):
        self.tools[n] = f

    def register_api(self, m, p, f):
        self.apis[(m, p)] = f

    def register_panel(self, p):
        self.panels.append(p)


class Req:
    def __init__(self, params=None, body=None):
        self.query_params = params or {}
        self._body = body or {}

    async def json(self):
        return self._body


class PluginImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ing_plugin_"))
        kbdir = self.tmp / "data" / "kb"
        kbdir.mkdir(parents=True)
        self.kb = KnowledgeBase(self.tmp)
        self._orig_kb = plugin._kb
        plugin._kb = lambda ctx: self.kb
        self.ctx = FakeCtx()

    def tearDown(self):
        plugin._kb = self._orig_kb
        self.kb.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_coro(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_import_base64_docx(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/import")]
        b64 = base64.b64encode(make_docx()).decode()
        res = self.run_coro(fn(Req(body={"name": "vpn.docx", "data_b64": b64})))
        self.assertTrue(res.get("ok"), res)
        self.assertIn("imported", res["path"])
        self.assertGreater(res["chars"], 10)
        self.assertGreaterEqual(res["stats"]["documents"], 1)

    def test_import_text(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/import")]
        res = self.run_coro(fn(Req(body={"text": "打印机脱机时重启 spooler", "title": "打印机"})))
        self.assertTrue(res.get("ok"))
        self.assertEqual(res["title"], "打印机")

    def test_import_requires_payload(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/import")]
        res = self.run_coro(fn(Req(body={})))
        self.assertFalse(res.get("ok"))

    def test_import_bad_base64(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/import")]
        res = self.run_coro(fn(Req(body={"name": "a.txt", "data_b64": "!!!notb64!!!"})))
        self.assertFalse(res.get("ok"))

    def test_import_url(self):
        plugin.setup(self.ctx)
        fn = self.ctx.apis[("POST", "api/import")]
        orig = ing.urllib.request.urlopen
        ing.urllib.request.urlopen = lambda *a, **k: FakeResponse(
            "<html><head><title>FAQ</title></head><body><p>VPN 端口 443</p></body></html>".encode("utf-8"))
        try:
            res = self.run_coro(fn(Req(body={"url": "https://example.com/faq"})))
        finally:
            ing.urllib.request.urlopen = orig
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(res["title"], "FAQ")

    def test_delete(self):
        plugin.setup(self.ctx)
        imp = self.ctx.apis[("POST", "api/import")]
        res = self.run_coro(imp(Req(body={"text": "临时文档", "title": "临时"})))
        rel = Path(res["path"]).relative_to(Path(self.kb.dir)).as_posix()
        dele = self.ctx.apis[("POST", "api/delete")]
        out = self.run_coro(dele(Req(body={"path": rel})))
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(out["stats"]["documents"], 0)

    def test_delete_rejects_escape(self):
        plugin.setup(self.ctx)
        dele = self.ctx.apis[("POST", "api/delete")]
        out = self.run_coro(dele(Req(body={"path": "../../config.json"})))
        self.assertFalse(out.get("ok"))
        self.assertIn("知识库目录", out.get("error", ""))

    def test_inbox_scan(self):
        plugin.setup(self.ctx)
        inbox = Path(self.kb.dir) / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "note.md").write_text("# 收件箱笔记\n换网线", encoding="utf-8")
        listing = self.run_coro(self.ctx.apis[("GET", "api/inbox")](Req()))
        self.assertEqual(listing["count"], 1)
        res = self.run_coro(self.ctx.apis[("POST", "api/inbox/scan")](Req(body={})))
        self.assertEqual(res["imported"], 1)
        self.assertFalse((inbox / "note.md").exists(), "导入后应移到 imported/")
        self.assertTrue((inbox / "imported" / "note.md").exists())


class _FakeOcr:
    """RapidOCR 3.x 形状：返回带 .txts 的对象。"""

    class _Out:
        def __init__(self, txts):
            self.txts = txts

    def __init__(self, lines):
        self.lines = lines
        self.calls = 0

    def __call__(self, arr):
        self.calls += 1
        return _FakeOcr._Out(list(self.lines))


class OcrTests(unittest.TestCase):
    def setUp(self):
        from gantry import kb_ocr

        self.kb_ocr = kb_ocr
        self._orig = kb_ocr._ENGINE
        kb_ocr.set_engine(None)

    def tearDown(self):
        self.kb_ocr.set_engine(self._orig)

    def _png(self) -> bytes:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (40, 20), "white").save(buf, format="PNG")
        return buf.getvalue()

    def test_png_goes_through_ocr(self):
        self.kb_ocr.set_engine(_FakeOcr(["打印机脱机", "重启 spooler"]))
        text = ing.extract_text("scan.png", self._png())
        self.assertIn("打印机脱机", text)
        self.assertIn("重启 spooler", text)

    def test_ocr_empty_result_reports(self):
        self.kb_ocr.set_engine(_FakeOcr([]))
        with self.assertRaises(ing.IngestError) as ctx:
            ing.extract_text("blank.png", self._png())
        self.assertIn("没有识别出文字", str(ctx.exception))

    def test_missing_engine_actionable(self):
        """没装 rapidocr 时：报错要给出 pip install。"""
        self.kb_ocr.set_engine(None)
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "rapidocr":
                raise ImportError("no module")
            return real_import(name, *a, **k)

        builtins.__import__ = fake_import
        try:
            with self.assertRaises(ing.IngestError) as ctx:
                self.kb_ocr.ocr_image_bytes(self._png())
        finally:
            builtins.__import__ = real_import
        self.assertIn("pip install rapidocr", str(ctx.exception))

    def test_scanned_pdf_falls_back_to_ocr(self):
        """PDF 没有文本层时，逐页取内嵌图交给 OCR。"""
        from fpdf import FPDF
        from PIL import Image

        tmp = Path(tempfile.mkdtemp(prefix="ocr_pdf_"))
        try:
            png = tmp / "page.png"
            Image.new("RGB", (200, 60), "white").save(png, format="PNG")
            pdf = FPDF()
            pdf.add_page()
            pdf.image(str(png), x=10, y=10, w=120)
            data = bytes(pdf.output())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.kb_ocr.set_engine(_FakeOcr(["扫描页文字"]))
        text = ing.extract_text("scan.pdf", data)
        self.assertIn("扫描页文字", text)
        self.assertIn("OCR", text)

    def test_text_pdf_does_not_ocr(self):
        """有文本层的 PDF 不该触发 OCR。"""
        engine = _FakeOcr(["不该出现"])
        self.kb_ocr.set_engine(engine)
        text = ing.extract_text("guide.pdf", make_pdf())
        self.assertIn("VPN troubleshooting", text)
        self.assertEqual(engine.calls, 0)


if __name__ == "__main__":
    unittest.main()
