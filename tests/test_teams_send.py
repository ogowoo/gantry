# -*- coding: utf-8 -*-
"""Teams 发送定位：标题校验容忍 "姓名 - 公司" 后缀。

深链（/l/chat/...）会跳转 Teams 桌面客户端启动器（实测），不适合 Web 自动化，
因此定位仍走列表行/片段/人名搜索；这里只钉住标题匹配的容错。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gantry.teams_send as ts  # noqa: E402


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

    def test_prefix_only_still_rejected(self):
        ok, _ = ts._title_matches("Lynn", {"peer": "Lynn Ling - Example"})
        self.assertFalse(ok)

    def test_topic_near_match_rejected(self):
        ok, _ = ts._title_matches("IT 支持群 2", {"topic": "IT 支持群"})
        self.assertFalse(ok, "topic 子串不应通过（会发到同名/近名群）")
        ok, _ = ts._title_matches("IT 支持群", {"topic": "IT 支持群"})
        self.assertTrue(ok)

    def test_peer_substring_rejected(self):
        ok, _ = ts._title_matches("AI GROUP Chat", {"peer": "AI"})
        self.assertFalse(ok, "peer 子串不应通过")


if __name__ == "__main__":
    unittest.main()
