# -*- coding: utf-8 -*-
"""Mentions 读取的纯逻辑：badge 解析 + 行规范化。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import teams_mentions as tm  # noqa: E402


class BadgeTests(unittest.TestCase):
    def test_badge_from_text(self):
        self.assertEqual(tm.badge_from_text("Mentions\n1"), 1)
        self.assertEqual(tm.badge_from_text("Mentions 12"), 12)
        self.assertEqual(tm.badge_from_text("Mentions"), 0)
        self.assertEqual(tm.badge_from_text(""), 0)


class RowTests(unittest.TestCase):
    def test_strips_own_name_and_dedupes(self):
        rows = [
            {"sender": "Chen, Alex", "message": "Alex Chen - Example 打印机脱机",
             "time": "9:37 PM"},
            {"sender": "Chen, Alex", "message": "Alex Chen - Example 打印机脱机",
             "time": "9:37 PM"},
            {"sender": "Chen, Alex", "message": "Alex Chen - Example 你怎么识别不了？",
             "time": "9:35 PM"},
        ]
        out = tm.normalise_rows(rows, "Alex Chen - Example")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["question"], "打印机脱机")
        self.assertEqual(out[1]["question"], "你怎么识别不了？")
        self.assertTrue(out[0]["key"])

    def test_without_my_name_keeps_message(self):
        out = tm.normalise_rows([{"sender": "A", "message": "Alex Chen - Example 你好",
                                  "time": "1:00 PM"}], "")
        self.assertEqual(out[0]["question"], "Alex Chen - Example 你好")

    def test_empty_message_skipped(self):
        self.assertEqual(tm.normalise_rows([{"sender": "A", "message": ""}], ""), [])


if __name__ == "__main__":
    unittest.main()
