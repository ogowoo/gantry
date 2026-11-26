# -*- coding: utf-8 -*-
"""copilot_file：让 Copilot 生成文件并下载到工作区的回归测试。

用本地 HTML 复刻回答区里的三种下载入口（blob: 链接 / data: 链接 /
下载按钮），直接驱动 gantry.bridge 的候选识别与落盘逻辑，断言：
  - blob: / data: 链接能被认出来并写成正确字节
  - 文件名匹配优先（多个入口时选中目标文件）
  - 关闭开关时工具不可用；工作区外的路径被沙箱拒绝
需要 playwright 与可用浏览器，缺失时自动跳过。
"""
import asyncio
import base64
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURE = """<!doctype html><html><head><meta charset="utf-8"><title>answer</title></head><body>
<div data-content="ai-message">
  <p>已为你生成文件。</p>
  <a id="other" download="other.txt" href="data:text/plain;base64,b3RoZXI=">other.txt</a>
  <a id="target" download="gen.py" href="data:text/plain;base64,{PAYLOAD}">gen.py</a>
</div>
<div data-content="ai-message">
  <button id="btn" aria-label="Download file">下载</button>
</div>
<script>
 document.getElementById('btn').addEventListener('click', () => {
   const blob = new Blob(["from-button"], {type: "text/plain"});
   const a = document.createElement('a');
   a.href = URL.createObjectURL(blob);
   a.download = "button.txt";
   document.body.appendChild(a);
   a.click();
 });
</script></body></html>
"""


def make_page(browser, payload: bytes):
    html = FIXTURE.replace("{PAYLOAD}", base64.b64encode(payload).decode())
    tmp = Path(tempfile.mkdtemp(prefix="cbp_cfile_"))
    f = tmp / "answer.html"
    f.write_text(html, encoding="utf-8")
    return f


class CopilotFileTests(unittest.TestCase):
    def _run(self, coro_factory):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("未安装 playwright")

        async def runner():
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(channel="msedge", headless=True)
                except Exception as exc:
                    self.skipTest(f"无可用浏览器: {exc}")
                result = await coro_factory(browser)
                await browser.close()
                return result

        return asyncio.run(runner())

    def test_data_link_downloaded_to_workspace(self):
        from gantry import bridge as b

        payload = "print('hello from copilot')\n".encode("utf-8")

        async def scenario(browser):
            page = await browser.new_page()
            html = make_page(browser, payload)
            await page.goto(html.as_uri())
            ws = Path(tempfile.mkdtemp(prefix="cbp_cfile_ws_"))
            saved = (b.page, b.ctx, b.AGENT_TOOLS.root, b.AGENT_CFG.get("allow_copilot_file"))
            b.page = page
            b.ctx = None                      # data:/blob: 路径不需要 ctx
            b.AGENT_TOOLS.root = ws
            b.AGENT_CFG["allow_copilot_file"] = True
            try:
                # 直接调内部保存逻辑，跳过与 Copilot 的往返（这一步才是本功能的易错点）
                cands = await b._download_candidates()
                self.assertTrue(cands, "应识别出下载入口")
                chosen = None
                for c in cands:
                    if "gen.py" in (c["text"] + c["href"]):
                        chosen = c
                        break
                self.assertIsNotNone(chosen, f"应优先选中文件名匹配的入口: {[c['text'] for c in cands]}")
                target = ws / "gen.py"
                size = await b._save_from_href(chosen["href"], target)
                return size, target.read_bytes()
            finally:
                b.page, b.ctx, b.AGENT_TOOLS.root = saved[0], saved[1], saved[2]
                b.AGENT_CFG["allow_copilot_file"] = saved[3]

        size, data = self._run(scenario)
        self.assertEqual(data, payload)
        self.assertEqual(size, len(payload))

    def test_download_over_cap_rejected(self):
        """超过 agent.copilot_file_max_mb 的下载必须被拒且不落盘（M21）。"""
        from gantry import bridge as b
        from gantry.downloads import _save_from_href

        payload = b"A" * (2 * 1024 * 1024)          # 2MB

        async def scenario(browser):
            page = await browser.new_page()
            html = make_page(browser, payload)
            await page.goto(html.as_uri())
            saved = (b.page, b.ctx, b.AGENT_TOOLS.root, b.AGENT_CFG.get("copilot_file_max_mb"))
            b.page = page
            b.ctx = None
            b.AGENT_CFG["copilot_file_max_mb"] = 1   # 上限 1MB
            try:
                target = b.AGENT_TOOLS.root / "big.bin"
                try:
                    await _save_from_href("data:application/octet-stream;base64,"
                                          + base64.b64encode(payload).decode(), target)
                    return "no-error"
                except Exception as exc:
                    return f"error:{exc}"
            finally:
                b.page, b.ctx = saved[0], saved[1]
                b.AGENT_TOOLS.root = saved[2]
                b.AGENT_CFG["copilot_file_max_mb"] = saved[3]

        out = self._run(scenario)
        self.assertTrue(str(out).startswith("error:"), f"超限下载应抛错，实际: {out}")
        self.assertIn("超过单次上限", str(out))

    def test_blob_link_downloaded(self):
        from gantry import bridge as b

        payload = b"blob-content-12345"

        async def scenario(browser):
            page = await browser.new_page()
            tmp = Path(tempfile.mkdtemp(prefix="cbp_cfile_"))
            f = tmp / "blob.html"
            f.write_text(
                "<!doctype html><div data-content='ai-message'>"
                "<a id='b' download='x.bin' href='#'>x.bin</a></div><script>"
                "const blob=new Blob([new Uint8Array([98,108,111,98,45,99,111,110,116,101,110,116])],"
                "{type:'application/octet-stream'});"
                "document.getElementById('b').href=URL.createObjectURL(blob);"
                "</script>", encoding="utf-8")
            await page.goto(f.as_uri())
            saved = (b.page,)
            b.page = page
            try:
                cands = await b._download_candidates()
                blob = [c for c in cands if c["href"].startswith("blob:")]
                self.assertTrue(blob, f"应识别 blob: 入口: {cands}")
                out = tmp / "out.bin"
                await b._save_from_href(blob[0]["href"], out)
                return out.read_bytes()
            finally:
                b.page = saved[0]

        self.assertEqual(self._run(scenario), b"blob-content")

    def test_disabled_flag_and_sandbox(self):
        from gantry import bridge as b
        from gantry.tools import ToolError
        saved = b.AGENT_CFG.get("allow_copilot_file")
        try:
            b.AGENT_CFG["allow_copilot_file"] = False
            with self.assertRaises(ToolError):
                asyncio.run(b._copilot_file("x.txt", "生成"))
        finally:
            b.AGENT_CFG["allow_copilot_file"] = saved
        # 工作区外的路径必须被沙箱拒绝（复用工作区 _path 校验）
        with self.assertRaises(ToolError):
            asyncio.run(b._copilot_file("../outside.txt", "生成"))


def _mk_zip(entries) -> Path:
    """entries 可以是 {name: bytes} 或 [(name, bytes), ...]（列表允许同名条目）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        items = entries.items() if isinstance(entries, dict) else entries
        for name, data in items:
            zf.writestr(name, data)
    d = Path(tempfile.mkdtemp(prefix="cbp_zip_"))
    z = d / "pack.zip"
    z.write_bytes(buf.getvalue())
    return z


def _mk_ws() -> Path:
    return Path(tempfile.mkdtemp(prefix="cbp_zip_ws_"))


class CopilotZipTests(unittest.TestCase):
    """zip 传输：Copilot 打包 → 本地安全解压的纯逻辑回归（无需浏览器）。"""

    def test_exact_name_extracted(self):
        from gantry.downloads import _extract_copilot_zip

        z = _mk_zip({"报告.xlsx": b"XLSXBYTES"})
        target = _mk_ws() / "报告.xlsx"
        out = _extract_copilot_zip(z, target, max_bytes=10_000_000)
        self.assertEqual(target.read_bytes(), b"XLSXBYTES")
        self.assertEqual(out["bytes"], 9)
        self.assertEqual(out["extracted"], ["报告.xlsx"])
        self.assertEqual(out["extras"], [])

    def test_single_file_renamed_adopted(self):
        """zip 里只有一个文件但名字与目标不同（Copilot 改名）→ 直接采用。"""
        from gantry.downloads import _extract_copilot_zip

        z = _mk_zip({"gen_final.docx": b"DOCX"})
        target = _mk_ws() / "gen.docx"
        _extract_copilot_zip(z, target, max_bytes=10_000_000)
        self.assertEqual(target.read_bytes(), b"DOCX")

    def test_extras_go_to_dedicated_dir(self):
        """多余文件统一进 <stem>_extras/，绝不落工作区根（防 sitecustomize 类导入）。"""
        from gantry.downloads import _extract_copilot_zip

        z = _mk_zip({"gen.docx": b"D", "data.csv": b"C"})
        ws = _mk_ws()
        target = ws / "gen.docx"
        out = _extract_copilot_zip(z, target, max_bytes=10_000_000)
        self.assertEqual((ws / "gen_extras" / "data.csv").read_bytes(), b"C")
        self.assertFalse((ws / "data.csv").exists(), "extras 不得落工作区根")
        self.assertEqual(out["extras"], [str(ws / "gen_extras" / "data.csv")])

    def test_extras_collision_preserves_existing(self):
        """工作区根已有同名文件时也不受影响（extras 本来就在子目录里）。"""
        from gantry.downloads import _extract_copilot_zip

        ws = _mk_ws()
        (ws / "data.csv").write_bytes(b"OLD")
        z = _mk_zip({"gen.docx": b"D", "data.csv": b"C"})
        target = ws / "gen.docx"
        _extract_copilot_zip(z, target, max_bytes=10_000_000)
        self.assertEqual((ws / "data.csv").read_bytes(), b"OLD", "不得覆盖工作区旧文件")
        self.assertEqual((ws / "gen_extras" / "data.csv").read_bytes(), b"C")

    def test_duplicate_entries_renamed_uniquely(self):
        """同名条目（含大小写不敏感）逐个重命名，互不覆盖、不丢失。"""
        from gantry.downloads import _extract_copilot_zip

        z = _mk_zip([("gen.docx", b"FIRST"), ("gen_2.docx", b"SECOND"),
                     ("gen.docx", b"THIRD"), ("GEN.DOCX", b"FOURTH")])
        ws = _mk_ws()
        out = _extract_copilot_zip(z, ws / "gen.docx", max_bytes=10_000_000)
        self.assertEqual((ws / "gen.docx").read_bytes(), b"FIRST")
        self.assertEqual((ws / "gen_extras" / "gen_2.docx").read_bytes(), b"SECOND")
        self.assertEqual((ws / "gen_extras" / "gen_3.docx").read_bytes(), b"THIRD")
        self.assertEqual((ws / "gen_extras" / "gen_4.docx").read_bytes(), b"FOURTH")
        self.assertEqual(len(out["extracted"]), 4)

    def test_sensitive_names_skipped(self):
        """凭据类条目一律不落盘，在 skipped_extras 里如实报告。"""
        from gantry.downloads import _extract_copilot_zip

        z = _mk_zip({"gen.docx": b"D", "config.json": b'{"api_key":"sk-x"}',
                     ".env": b"TOKEN=1", "id_rsa": b"PRIV"})
        ws = _mk_ws()
        out = _extract_copilot_zip(z, ws / "gen.docx", max_bytes=10_000_000)
        self.assertEqual((ws / "gen.docx").read_bytes(), b"D")
        self.assertFalse((ws / "gen_extras").exists(), "凭据类条目不得落盘")
        self.assertEqual(sorted(out["skipped_extras"]), [".env", "config.json", "id_rsa"])

    def test_all_sensitive_zip_rejected(self):
        from gantry.downloads import _extract_copilot_zip
        from gantry.tools import ToolError

        z = _mk_zip({"config.json": b"{}", ".env": b"A=1"})
        ws = _mk_ws()
        with self.assertRaises(ToolError) as cm:
            _extract_copilot_zip(z, ws / "gen.docx", max_bytes=10_000_000)
        self.assertIn("全部被安全规则跳过", str(cm.exception))

    def test_missing_target_multiple_files_errors(self):
        from gantry.downloads import _extract_copilot_zip
        from gantry.tools import ToolError

        z = _mk_zip({"a.txt": b"A", "b.txt": b"B"})
        ws = _mk_ws()
        target = ws / "gen.docx"
        with self.assertRaises(ToolError) as cm:
            _extract_copilot_zip(z, target, max_bytes=10_000_000)
        self.assertIn("zip 里没有 gen.docx", str(cm.exception))
        self.assertEqual(list(ws.iterdir()), [], "失败时工作区不能留半成品")

    def test_zip_slip_rejected(self):
        from gantry.downloads import _extract_copilot_zip
        from gantry.tools import ToolError

        for evil in ("../evil.txt", "/abs.txt", "C:/win.txt"):
            z = _mk_zip({evil: b"E"})
            ws = _mk_ws()
            with self.assertRaises(ToolError, msg=f"应拒绝 {evil!r}"):
                _extract_copilot_zip(z, ws / "gen.docx", max_bytes=10_000_000)
            self.assertEqual(list(ws.iterdir()), [])

    def test_not_a_zip_errors(self):
        from gantry.downloads import _extract_copilot_zip
        from gantry.tools import ToolError

        d = Path(tempfile.mkdtemp(prefix="cbp_zip_"))
        f = d / "pack.zip"
        f.write_bytes(b"this is not a zip at all")
        with self.assertRaises(ToolError) as cm:
            _extract_copilot_zip(f, _mk_ws() / "gen.docx", max_bytes=10_000_000)
        self.assertIn("不是有效 zip", str(cm.exception))

    def test_uncompressed_over_cap_rejected(self):
        from gantry.downloads import _extract_copilot_zip
        from gantry.tools import ToolError

        z = _mk_zip({"gen.bin": b"A" * 4096})
        ws = _mk_ws()
        with self.assertRaises(ToolError) as cm:
            _extract_copilot_zip(z, ws / "gen.bin", max_bytes=1024)
        self.assertIn("超过单次上限", str(cm.exception))
        self.assertFalse((ws / "gen.bin").exists())


class CopilotFileZipTransportTests(unittest.TestCase):
    """copilot_file 全链路：提示词要求 zip + 下载后解压落盘（浏览器驱动）。"""

    def _run(self, coro_factory):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.skipTest("未安装 playwright")

        async def runner():
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(channel="msedge", headless=True)
                except Exception as exc:
                    self.skipTest(f"无可用浏览器: {exc}")
                result = await coro_factory(browser)
                await browser.close()
                return result

        return asyncio.run(runner())

    def test_asks_for_zip_and_extracts(self):
        from gantry import bridge as b

        zipbuf = io.BytesIO()
        with zipfile.ZipFile(zipbuf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("gen.py", "print('zip ok')\n")
        payload64 = base64.b64encode(zipbuf.getvalue()).decode()
        html = f"""<!doctype html><html><body>
<div data-content="ai-message">
  <p>已为你生成文件。</p>
  <a id="target" download="gen.zip"
     href="data:application/zip;base64,{payload64}">gen.zip</a>
</div></body></html>"""

        async def scenario(browser):
            page = await browser.new_page()
            tmp = Path(tempfile.mkdtemp(prefix="cbp_cfile_"))
            f = tmp / "answer.html"
            f.write_text(html, encoding="utf-8")
            await page.goto(f.as_uri())
            ws = _mk_ws()
            saved = (b.page, b.ctx, b.AGENT_TOOLS.root, b.agent_chat_once,
                     b.AGENT_CFG.get("allow_copilot_file"))
            b.page = page
            b.ctx = None
            b.AGENT_TOOLS.root = ws
            b.AGENT_CFG["allow_copilot_file"] = True
            captured = {}

            async def fake_chat(prompt, model, new, timeout=None):
                captured["prompt"] = prompt
                return "ok"

            b.agent_chat_once = fake_chat
            try:
                out = await b._copilot_file("gen.py", "生成 hello 脚本")
                return captured.get("prompt", ""), out, ws
            finally:
                b.page, b.ctx, b.AGENT_TOOLS.root, b.agent_chat_once = saved[:4]
                if saved[4] is None:
                    b.AGENT_CFG.pop("allow_copilot_file", None)
                else:
                    b.AGENT_CFG["allow_copilot_file"] = saved[4]

        prompt, out, ws = self._run(scenario)
        self.assertIn("zip", prompt, "提示词必须要求打包 zip")
        self.assertIn("gen.zip", prompt)
        self.assertIn("gen.py", prompt)
        self.assertEqual((ws / "gen.py").read_bytes(), b"print('zip ok')\n")
        self.assertEqual(out["bytes"], 16)
        self.assertEqual(out["source"], "copilot-download-zip")
        self.assertEqual(out["extracted"], ["gen.py"])

    def _zip_payload(self, content: bytes) -> str:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("gen.py", content)
        return base64.b64encode(buf.getvalue()).decode()

    async def _scenario(self, browser, html, fname="gen.py"):
        from gantry import bridge as b

        page = await browser.new_page()
        tmp = Path(tempfile.mkdtemp(prefix="cbp_cfile_"))
        f = tmp / "answer.html"
        f.write_text(html, encoding="utf-8")
        await page.goto(f.as_uri())
        ws = _mk_ws()
        saved = (b.page, b.ctx, b.AGENT_TOOLS.root, b.agent_chat_once,
                 b.AGENT_CFG.get("allow_copilot_file"))
        b.page = page
        b.ctx = None
        b.AGENT_TOOLS.root = ws
        b.AGENT_CFG["allow_copilot_file"] = True

        async def fake_chat(prompt, model, new, timeout=None):
            return "ok"

        b.agent_chat_once = fake_chat
        try:
            out = await b._copilot_file(fname, "生成 hello 脚本")
            return out, ws
        finally:
            b.page, b.ctx, b.AGENT_TOOLS.root, b.agent_chat_once = saved[:4]
            if saved[4] is None:
                b.AGENT_CFG.pop("allow_copilot_file", None)
            else:
                b.AGENT_CFG["allow_copilot_file"] = saved[4]

    def test_zip_candidate_preferred_over_direct(self):
        """页面同时有真文件直链与 zip 时，必须选 zip 解压，而不是直存真文件。"""
        html = ("""<!doctype html><html><body><div data-content="ai-message">
<a id="direct" download="gen.py"
   href="data:text/plain;base64,{direct}">gen.py</a>
<a id="zip" download="gen.zip"
   href="data:application/zip;base64,{zip64}">gen.zip</a>
</div></body></html>""".format(
            direct=base64.b64encode(b"print('direct')\n").decode(),
            zip64=self._zip_payload(b"print('from zip')\n")))

        async def coro(browser):
            return await self._scenario(browser, html)

        out, ws = self._run(coro)
        self.assertEqual(out["source"], "copilot-download-zip")
        self.assertEqual((ws / "gen.py").read_bytes(), b"print('from zip')\n")

    def test_bad_zip_candidate_falls_to_next(self):
        """第一个 zip 名候选内容不是 zip（预览劫持形态）→ 换下一个候选成功。"""
        html = ("""<!doctype html><html><body><div data-content="ai-message">
<a id="junk" download="gen.zip"
   href="data:text/plain;base64,{junk}">gen.zip</a>
<a id="real" download="gen_real.zip"
   href="data:application/zip;base64,{zip64}">gen_real.zip</a>
</div></body></html>""".format(
            junk=base64.b64encode(b"not a zip").decode(),
            zip64=self._zip_payload(b"print('real zip')\n")))

        async def coro(browser):
            return await self._scenario(browser, html)

        out, ws = self._run(coro)
        self.assertEqual(out["source"], "copilot-download-zip")
        self.assertEqual((ws / "gen.py").read_bytes(), b"print('real zip')\n")

    def test_direct_fallback_when_no_zip(self):
        """Copilot 无视打包要求、只给了目标直链 → 退化直存成功（旧行为不丢）。"""
        html = ("""<!doctype html><html><body><div data-content="ai-message">
<a id="direct" download="gen.py"
   href="data:text/plain;base64,{direct}">gen.py</a>
</div></body></html>""".format(
            direct=base64.b64encode(b"print('direct')\n").decode()))

        async def coro(browser):
            return await self._scenario(browser, html)

        out, ws = self._run(coro)
        self.assertEqual(out["source"], "copilot-download-direct")
        self.assertEqual((ws / "gen.py").read_bytes(), b"print('direct')\n")


if __name__ == "__main__":
    unittest.main()
