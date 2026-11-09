# -*- coding: utf-8 -*-
"""Teams 发送定位：标题容忍 "姓名 - 公司" 后缀 + 会话 id 深链。"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gantry.teams_send as ts  # noqa: E402


class FakePage:
    def __init__(self):
        self.urls = []

    async def goto(self, url, **kwargs):
        self.urls.append(url)


class TitleMatchTests(unittest.TestCase):
    def test_full_name_matches(self):
        ok, why = ts._title_matches("Lynn Ling - Example", {"peer": "Lynn Ling - Example"})
        self.assertTrue(ok, why)

    def test_header_without_company_suffix_still_matches(self):
        """IDB peerName 带 - Example，聊天头部可能只显示姓名。"""
        ok, why = ts._title_matches("Lynn Ling", {"peer": "Lynn Ling - Example"})
        self.assertTrue(ok, why)

    def test_different_person_still_rejected(self):
        ok, _ = ts._title_matches("AI GROUP Chat", {"peer": "Lynn Ling - Example"})
        self.assertFalse(ok)


class DeepLinkTests(unittest.TestCase):
    def test_deeplink_opens_matching_chat(self):
        page = FakePage()
        old = ts.header_title

        async def fake_head(_page):
            return "Lynn Ling"

        ts.header_title = fake_head
        try:
            res = asyncio.run(ts.open_chat_by_deeplink(
                "19:abc_123@unq.gbl.spaces", page=page,
                expected={"peer": "Lynn Ling - Example"}))
        finally:
            ts.header_title = old
        self.assertTrue(res["opened"], res)
        self.assertEqual(res["how"], "deeplink")
        self.assertIn("teams.cloud.microsoft/l/chat/19:abc_123@unq.gbl.spaces/conversations",
                      page.urls[0])

    def test_deeplink_rejects_wrong_chat(self):
        page = FakePage()
        old = ts.header_title

        async def fake_head(_page):
            return "AI GROUP Chat"

        ts.header_title = fake_head
        try:
            res = asyncio.run(ts.open_chat_by_deeplink(
                "19:abc_123@unq.gbl.spaces", page=page,
                expected={"peer": "Lynn Ling - Example"}, wait=0.1))
        finally:
            ts.header_title = old
        self.assertFalse(res["opened"])
        self.assertEqual(len(page.urls), 2, "应在两个域名上都尝试")


if __name__ == "__main__":
    unittest.main()
