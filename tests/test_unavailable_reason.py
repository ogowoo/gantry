# -*- coding: utf-8 -*-
"""copilot_unavailable_reason：服务限流/不可用提示的识别（含 2026-11 新增的每日上限）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import api  # noqa: F401  先导入 api：routes_* 与 api 互相引用（包内约定）
from gantry import bridge as b


class UnavailableReasonTests(unittest.TestCase):
    def test_daily_chat_limit_recognized(self):
        # 用户实测原文：2026-11 起 Copilot 新增的每日聊天上限
        text = "You've used your daily chat limit. Please try again tomorrow."
        self.assertTrue(b.copilot_unavailable_reason(text))

    def test_daily_limit_with_surrounding_noise(self):
        text = ("Copilot\nYou've used your daily chat limit. "
                "Please try again tomorrow.\nTry again\n0/30")
        self.assertTrue(b.copilot_unavailable_reason(text))

    def test_normal_answer_not_flagged(self):
        self.assertFalse(b.copilot_unavailable_reason(
            "The fix is to retry the request after the rate settles. See below."))

    def test_agent_action_json_not_flagged(self):
        # 正常 Agent 回答即使提到 limit 也不能误判（含 type 字段的一律豁免）
        text = '{"type":"final","content":"daily limit note"}'
        self.assertFalse(b.copilot_unavailable_reason(text))

    def test_classic_rate_limit_still_recognized(self):
        self.assertTrue(b.copilot_unavailable_reason(
            "Sorry, something went wrong. Please try again later."))


if __name__ == "__main__":
    unittest.main()
