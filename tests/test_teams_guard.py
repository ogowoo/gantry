# -*- coding: utf-8 -*-
"""Teams 发送前身份核验与页面互斥锁（纯离线）。"""
import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import teams  # noqa: E402

TENANT = "5c899237-c5e3-44bf-8b5f-8f7e24b3e5d1"
PEER = "fd41f5b9-3975-446f-b1c4-8126101f98ec"
CID = f"19:{TENANT}_{PEER}@unq.gbl.spaces"


def conv(**kw):
    base = {"id": CID, "topic": "", "lastFrom": "Lynn Ling - DSV",
            "last": "<p>@Gantry VPN 连不上</p>"}
    base.update(kw)
    return base


class IdentityTests(unittest.TestCase):
    def test_exact_match_ok(self):
        res = teams.verify_conversation_identity(
            conv(), {"conv_id": CID, "peer": "Lynn Ling - DSV",
                     "peer_guid": PEER, "snippet": "@Gantry VPN 连不上"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["errors"], [])

    def test_wrong_conversation_id(self):
        res = teams.verify_conversation_identity(
            conv(), {"conv_id": "19:other@thread.v2"})
        self.assertFalse(res["ok"])
        self.assertIn("conversation_id", " ".join(res["errors"]))

    def test_topic_mismatch(self):
        res = teams.verify_conversation_identity(
            conv(topic="AI GROUP Chat"), {"conv_id": CID, "topic": "IT 支持群"})
        self.assertFalse(res["ok"])
        self.assertIn("标题", " ".join(res["errors"]))

    def test_peer_guid_mismatch(self):
        res = teams.verify_conversation_identity(
            conv(), {"conv_id": CID, "peer_guid": "00000000-0000-0000-0000-000000000000"})
        self.assertFalse(res["ok"])
        self.assertIn("GUID", " ".join(res["errors"]))

    def test_snippet_mismatch(self):
        res = teams.verify_conversation_identity(
            conv(), {"conv_id": CID, "snippet": "完全不同的问题"})
        self.assertFalse(res["ok"])
        self.assertIn("消息内容", " ".join(res["errors"]))

    def test_title_matches_full_peer_not_prefix(self):
        self.assertTrue(teams._title_matches("Lynn Ling - DSV", {"peer": "Lynn Ling - DSV"})[0])
        self.assertFalse(teams._title_matches("Lynn", {"peer": "Lynn Ling - DSV"})[0])


class PageLockTests(unittest.TestCase):
    def test_lock_serializes_and_times_out(self):
        async def run():
            await teams.acquire_page_lock(timeout=1)
            try:
                with self.assertRaises(teams.TeamsBusyError):
                    await teams.acquire_page_lock(timeout=0.05)
            finally:
                teams.release_page_lock()
            # 释放后可以再拿
            await teams.acquire_page_lock(timeout=1)
            teams.release_page_lock()

        asyncio.new_event_loop().run_until_complete(run())


class TabReuseTests(unittest.TestCase):
    class Page:
        def __init__(self, url, closed=False):
            self.url = url
            self._closed = closed

        def is_closed(self):
            return self._closed

    def test_finds_existing_teams_tab(self):
        class Ctx:
            pages = [TabReuseTests.Page("https://m365.cloud.microsoft/chat/"),
                     TabReuseTests.Page("https://teams.cloud.microsoft/v2/")]

        page = asyncio.new_event_loop().run_until_complete(
            teams._existing_teams_page(Ctx()))
        self.assertTrue(page.url.startswith("https://teams."))

    def test_skips_closed_teams_tab(self):
        class Ctx:
            pages = [TabReuseTests.Page("https://teams.cloud.microsoft/v2/", closed=True)]

        page = asyncio.new_event_loop().run_until_complete(
            teams._existing_teams_page(Ctx()))
        self.assertIsNone(page)


if __name__ == "__main__":
    unittest.main()
