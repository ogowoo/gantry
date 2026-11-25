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
        self.handlers["Page.screencastFrame"]({
            "data": data, "sessionId": sid,
            "metadata": {"deviceWidth": 1280, "deviceHeight": 720},
        })

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
        # 帧是 dict：base64 画面 + 页面视口 CSS 像素（v2 互动坐标换算用）
        self.assertEqual(frames[:2], [
            {"img": "Zmlyc3Q=", "w": 1280, "h": 720},
            {"img": "c2Vjb25k", "w": 1280, "h": 720},
        ])
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
            yield {"img": "Zmlyc3Q=", "w": 1280, "h": 720}
            yield None                       # 心跳

        with mock.patch.object(liveview, "stream_frames", fake_frames):
            resp = asyncio.run(routes_live.live_stream(index=0))

            async def read_body():
                chunks = []
                async for c in resp.body_iterator:
                    chunks.append(c)
                return chunks

            body = asyncio.run(read_body())
        self.assertEqual(body, [
            'data: {"img":"Zmlyc3Q=","w":1280,"h":720}\n\n', ": ping\n\n"])

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


class _InputFakePage(_FakePage):
    """带 mouse/keyboard 记录的假页面，断言转发动作与坐标。"""

    def __init__(self, url, title):
        super().__init__(url, title)
        self.calls = []
        page = self
        self.mouse = mock.Mock()
        self.mouse.click = self._rec("click")
        self.mouse.move = self._rec("move")
        self.mouse.wheel = self._rec("wheel")
        self.keyboard = mock.Mock()
        self.keyboard.press = self._rec("press")
        self.keyboard.insert_text = self._rec("insert_text")

    def _rec(self, name):
        async def fn(*a, **kw):
            self.calls.append((name, a, kw))
        return fn

    async def noop(self):
        pass


class TestInputEvent(unittest.TestCase):
    def _run(self, ev, index=0):
        ctx = _FakeCtx([_InputFakePage("https://a.com", "A")])
        with _patch_ctx(ctx):
            r = asyncio.run(liveview.input_event(index, ev))
        return r, ctx.pages[0].calls

    def test_click(self):
        r, calls = self._run({"kind": "click", "x": 100, "y": 50, "click_count": 2})
        self.assertTrue(r["ok"])
        self.assertEqual(calls, [("click", (100.0, 50.0), {"click_count": 2})])

    def test_wheel_moves_then_wheels(self):
        _, calls = self._run({"kind": "wheel", "x": 10, "y": 20, "delta_y": 300})
        self.assertEqual([c[0] for c in calls], ["move", "wheel"])
        self.assertEqual(calls[1][1], (0.0, 300.0))

    def test_key_allowed(self):
        _, calls = self._run({"kind": "key", "key": "Enter"})
        self.assertEqual(calls, [("press", ("Enter",), {})])

    def test_key_rejected(self):
        with self.assertRaises(ValueError):
            self._run({"kind": "key", "key": "F12"})

    def test_text(self):
        _, calls = self._run({"kind": "text", "text": "你好"})
        self.assertEqual(calls, [("insert_text", ("你好",), {})])

    def test_unknown_kind_and_bad_index(self):
        with self.assertRaises(ValueError):
            self._run({"kind": "drag"})
        ctx = _FakeCtx([_FakePage("https://a.com", "A")])
        with _patch_ctx(ctx):
            with self.assertRaises(KeyError):
                asyncio.run(liveview.input_event(5, {"kind": "click"}))

    def test_route_error_codes(self):
        from gantry import routes_live
        with mock.patch.object(liveview, "input_event",
                               new=mock.AsyncMock(side_effect=ValueError("未知输入类型：drag"))):
            with self.assertRaises(HTTPException) as cm:
                asyncio.run(routes_live.live_input({"index": 0, "kind": "drag"}))
        self.assertEqual(cm.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
