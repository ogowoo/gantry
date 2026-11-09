# -*- coding: utf-8 -*-
"""open_chat_by_thread_id：按 data-fui-tree-item-value 精确点击的单测。"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gantry.teams_dom as dom  # noqa: E402


class FakeTitle:
    def __init__(self):
        self.clicked = 0

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def click(self):
        self.clicked += 1


class FakeRow:
    def __init__(self):
        self.title = FakeTitle()
        self.hovered = 0

    @property
    def first(self):
        return self

    async def count(self):
        return 0

    async def hover(self):
        self.hovered += 1

    async def click(self, **kwargs):
        self.title.clicked += 1

    def locator(self, sel):
        return self.title


class FakeMouse:
    def __init__(self):
        self.wheels = []
        self.clicks = []

    async def wheel(self, x, y):
        self.wheels.append((x, y))

    async def click(self, x, y):
        self.clicks.append((x, y))


class FakePage:
    def __init__(self, hit):
        self.hit = hit
        self.row = FakeRow()
        self.mouse = FakeMouse()

    def locator(self, sel):
        return self.row

    def get_by_role(self, role, name=None):
        return self.row

    async def evaluate(self, js, arg=None):
        return self.hit


class ThreadIdTests(unittest.TestCase):
    def run_with(self, hit, header="Peer Name"):
        page = FakePage(hit)
        old = dom.header_title

        async def fake_head(_page):
            return header

        dom.header_title = fake_head
        try:
            out = asyncio.run(dom.open_chat_by_thread_id(
                "19:tenant_peer@unq.gbl.spaces", page=page, max_scroll=0))
        finally:
            dom.header_title = old
        return out, page

    def test_hit_clicks_title_and_returns_chat(self):
        out, page = self.run_with(
            {"x": 10, "y": 20, "w": 200, "h": 40, "text": "Peer Name\n10:10 PM"})
        self.assertTrue(out["opened"], out)
        self.assertEqual(out["chat"], "Peer Name")
        self.assertEqual(page.row.title.clicked, 1)

    def test_miss_returns_error_and_scrolls(self):
        out, page = self.run_with(None)
        self.assertFalse(out["opened"])
        self.assertIn("没找到", out["error"])
        self.assertTrue(page.mouse.wheels, "应在左侧列表滚动查找")


if __name__ == "__main__":
    unittest.main()
