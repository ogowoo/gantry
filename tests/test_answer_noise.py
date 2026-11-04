# -*- coding: utf-8 -*-
"""答案噪声：CBP 标记 / 无障碍提示 / 自己的问题回声都不能当成回答。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class CleanAnswerTests(unittest.TestCase):
    def test_strips_markers_and_ui_hint(self):
        from gantry.bridge import _clean_answer
        raw = ("[CBP-AGENT][task-x][C000]\n只回一个词：banana"
               "Press Tab to access the More options button.")
        out = _clean_answer(raw)
        self.assertNotIn("[CBP-AGENT]", out)
        self.assertNotIn("Press Tab", out)
        self.assertIn("banana", out)

    def test_keeps_real_answer(self):
        from gantry.bridge import _clean_answer
        self.assertEqual(_clean_answer('[CBP-TURN:abc]\n{"type":"final"}'), '{"type":"final"}')

    def test_echo_only_detected(self):
        from gantry.bridge import _echo_only
        sent = "只回一个词：banana"
        echo = ("[CBP-AGENT][task-x][C000]\n只回一个词：banana"
                "Press Tab to access the More options button.")
        self.assertTrue(_echo_only(echo, sent), "只是自己的问题回声")
        self.assertFalse(_echo_only("banana", sent))
        self.assertFalse(_echo_only('{"type":"final","content":"ok"}', sent))

    def test_wait_agent_answer_rejects_echo(self):
        src = ((ROOT / "gantry" / "bridge.py").read_text(encoding="utf-8")
               + (ROOT / "gantry" / "agentrun.py").read_text(encoding="utf-8"))
        self.assertIn("_echo_only(value, sent)", src)
        i = src.find("async def wait_agent_answer(")
        self.assertIn("sent: str = \"\"", src[i:i + 200])


class SuggestionTailTests(unittest.TestCase):
    def test_teams_reply_suggestions_stripped(self):
        """实测 Teams 回复尾巴混进了 Copilot 的建议按钮文字。"""
        from gantry.bridge import strip_suggestion_tail
        raw = (
            "收到，这是一次 Teams 自动回复测试。\n"
            "如果你能看到这条回复，说明自动回复功能当前正常。\n"
            "如需进一步验证，请再发送一条不同内容的测试。\n"
            "若结果异常，请截图报错信息并联系 IT 服务台协助排查。\n\n"
            "再测试一次自动回复\n帮我检查自动回复内容\n给我一版更简短的回复"
        )
        out = strip_suggestion_tail(raw)
        self.assertNotIn("再测试一次自动回复", out)
        self.assertNotIn("帮我检查自动回复内容", out)
        self.assertIn("自动回复功能当前正常", out)


if __name__ == "__main__":
    unittest.main()
