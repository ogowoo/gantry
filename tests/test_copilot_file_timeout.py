# -*- coding: utf-8 -*-
"""回归：copilot_file 生成回合必须有独立的长无进度超时。

用户实测：让 Copilot 生成带联网检索的 PDF，180s（reply_timeout_seconds）
无进度即被误杀——文件其实还在生成，且失败后页面状态连锁导致"Copilot 未就绪"。
要求：
  - wait_agent_answer 支持按回合覆盖无进度超时（idle_timeout 局部量）
  - agent_chat_once 把 timeout 透传下去
  - copilot_file 走自己的超时档（agent.copilot_file_timeout_seconds，默认 600）
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AGENTRUN = (ROOT / "gantry" / "agentrun.py").read_text(encoding="utf-8")
DOWNLOADS = (ROOT / "gantry" / "downloads.py").read_text(encoding="utf-8")


class CopilotFileTimeoutTests(unittest.TestCase):
    def test_wait_agent_answer_timeout_param(self):
        i = AGENTRUN.index("def wait_agent_answer(")
        seg = AGENTRUN[i:i + 8000]
        self.assertIn("timeout: float | None = None", seg)
        self.assertIn("idle_timeout = float(timeout) if timeout else float(b.TIMEOUT)", seg,
                      "按回合覆盖必须落到局部 idle_timeout，而不是改全局 b.TIMEOUT")
        # 函数体内的空闲截止与报错文案都用局部量
        self.assertIn("idle_deadline = started_at + idle_timeout", seg)
        self.assertIn("{int(idle_timeout)} 秒没有产生新的回答进度", seg)

    def test_agent_chat_once_threads_timeout(self):
        i = AGENTRUN.index("def agent_chat_once(")
        seg = AGENTRUN[i:i + 2500]
        self.assertIn("timeout: float | None = None", seg)
        self.assertIn("timeout=timeout", seg, "agent_chat_once 必须把 timeout 透传给 wait_agent_answer")

    def test_copilot_file_uses_own_timeout(self):
        i = DOWNLOADS.index("async def _copilot_file(")
        seg = DOWNLOADS[i:i + 3000]
        self.assertIn("timeout=await _copilot_file_timeout_seconds()", seg,
                      "copilot_file 生成回合必须用自己的超时档，不吃普通问答的 180s")
        j = DOWNLOADS.index("async def _copilot_file_timeout_seconds(")
        helper = DOWNLOADS[j:j + 900]
        self.assertIn("copilot_file_timeout_seconds", helper)
        self.assertIn("600", helper, "默认 600s")
        self.assertIn("max(60.0, sec)", helper, "下限 60s，避免配成 0 卡死")


if __name__ == "__main__":
    unittest.main()
