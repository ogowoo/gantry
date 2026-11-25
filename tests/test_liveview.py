# -*- coding: utf-8 -*-
"""liveview：URL 校验、页面列表、SSE 流格式、路由错误码（浏览器全 mock）。"""
import asyncio
import unittest
from unittest import mock

from fastapi import HTTPException

from gantry import liveview


class _FakePage:
    def __init__(self, url, title, closed=False):
        self.url = url
        self._title = title
        self._closed = closed

    def is_closed(self):
        return self._closed

    async def title(self):
        return self._title

    async def goto(self, url, wait_until=None, timeout=None):
        self.url = url


class _FakeCtx:
    def __init__(self, pages):
        self.pages = pages
        self.new_page_calls = 0

    async def new_page(self):
        self.new_page_calls += 1
        p = _FakePage("about:blank", "")
        self.pages.append(p)
        return p


def _patch_ctx(ctx):
    return mock.patch.object(liveview, "_ctx", new=mock.AsyncMock(return_value=ctx))


class TestOpenUrl(unittest.TestCase):
    def test_rejects_non_http(self):
        for bad in ("file:///C:/x", "javascript:alert(1)", "ftp://h/x", ""):
            with self.assertRaises(ValueError, msg=bad):
                asyncio.run(liveview.open_url(bad))

    def test_opens_and_records_last_open(self):
        ctx = _FakeCtx([_FakePage("https://copilot.example", "Copilot")])
        with _patch_ctx(ctx):
            r = asyncio.run(liveview.open_url("https://example.com/a"))
        self.assertTrue(r["ok"])
        self.assertEqual(ctx.new_page_calls, 1)
        lo = liveview.last_open()
        self.assertEqual(lo["url"], "https://example.com/a")
        self.assertGreater(lo["at"], 0)


class TestListPages(unittest.TestCase):
    def test_skips_closed_and_reports(self):
        ctx = _FakeCtx([
            _FakePage("https://a.com", "A"),
            _FakePage("https://b.com", "B", closed=True),
            _FakePage("https://c.com", "C" * 200),
        ])
        with _patch_ctx(ctx):
            d = asyncio.run(liveview.list_pages())
        urls = [p["url"] for p in d["pages"]]
        self.assertEqual(urls, ["https://a.com", "https://c.com"])
        # 原始下标保留（跳过的关闭页不占位），标题截断
        self.assertEqual([p["index"] for p in d["pages"]], [0, 2])
        self.assertEqual(len(d["pages"][1]["title"]), 80)


class _FakeCdp:
    def __init__(self, frames):
        self._frames = list(frames)
        self.handlers = {}
        self.sent = []

    def on(self, event, fn):
        self.handlers[event] = fn

    async def send(self, method, params=None):
        self.sent.append(method)
        if method == "Page.startScreencast":
            # 模拟 CDP 推两帧（含 ack 回环），然后静止
            loop = asyncio.get_running_loop()
            for i, f in enumerate(self._frames):
                loop.call_later(0.01 * (i + 1), self._fire, f, i)
        return {}

    def _fire(self, data, sid):
        self.handlers["Page.screencastFrame"]({"data": data, "sessionId": sid})

    async def detach(self):
        self.sent.append("detach")


class _CastCtx(_FakeCtx):
    def __init__(self, pages, cdp):
        super().__init__(pages)
        self._cdp = cdp

    async def new_cdp_session(self, page):
        return self._cdp


class TestStreamFrames(unittest.TestCase):
    def test_yields_frames_and_acks(self):
        cdp = _FakeCdp(["Zmlyc3Q=", "c2Vjb25k"])   # base64: first / second
        ctx = _CastCtx([_FakePage("https://a.com", "A")], cdp)

        async def run():
            out = []
            async for f in liveview.stream_frames(0):
                out.append(f)
                if len([x for x in out if x]) >= 2:
                    break
            return out

        with _patch_ctx(ctx):
            frames = asyncio.run(run())
        self.assertEqual(frames[:2], ["Zmlyc3Q=", "c2Vjb25k"])
        self.assertIn("Page.startScreencast", cdp.sent)
        self.assertEqual(cdp.sent.count("Page.screencastFrameAck"), 2)

    def test_bad_index_raises(self):
        ctx = _CastCtx([_FakePage("https://a.com", "A")], _FakeCdp([]))
        with _patch_ctx(ctx):
            with self.assertRaises(KeyError):
                asyncio.run(self._collect(liveview.stream_frames(9)))

    @staticmethod
    async def _collect(agen):
        async for _ in agen:
            pass


class TestRoutes(unittest.TestCase):
    def test_open_requires_url(self):
        from gantry import routes_live
        with self.assertRaises(HTTPException) as cm:
            asyncio.run(routes_live.live_open({}))
        self.assertEqual(cm.exception.status_code, 400)

    def test_open_value_error_is_400(self):
        from gantry import routes_live
        with mock.patch.object(liveview, "open_url",
                               new=mock.AsyncMock(side_effect=ValueError("只支持 http/https 链接"))):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(routes_live.live_open({"url": "file:///x"}))
        self.assertEqual(cm.exception.status_code, 400)

    def test_stream_route_wraps_frames_as_sse(self):
        from gantry import routes_live

        async def fake_frames(index):
            yield "Zmlyc3Q="
            yield None                       # 心跳

        with mock.patch.object(liveview, "stream_frames", fake_frames):
            resp = asyncio.run(routes_live.live_stream(index=0))

            async def read_body():
                chunks = []
                async for c in resp.body_iterator:
                    chunks.append(c)
                return chunks

            body = asyncio.run(read_body())
        self.assertEqual(body, ["data: Zmlyc3Q=\n\n", ": ping\n\n"])

    def test_plugin_registers_open_site(self):
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            "live_browser_plugin",
            pathlib.Path(__file__).parent.parent / "plugins" / "live-browser" / "plugin.py")
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        registered = {}

        class _Ctx:
            def register_tool(self, name, fn, desc, params):
                registered[name] = (fn, desc)

        mod.setup(_Ctx())
        self.assertIn("open_site", registered)
        fn, _ = registered["open_site"]
        with mock.patch.object(liveview, "open_url",
                               new=mock.AsyncMock(return_value={"ok": True, "url": "https://x.com", "title": "X"})):
            r = asyncio.run(fn("https://x.com"))
        self.assertEqual(r["opened"], "https://x.com")


if __name__ == "__main__":
    unittest.main()
