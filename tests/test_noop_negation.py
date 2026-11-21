# -*- coding: utf-8 -*-
"""否定动作词不能被当成“必须动手”。"""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.agent import AgentRunner
from gantry.tools import ToolRegistry


class NoopNegationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="noop_neg_"))
        self.state = {"state": "ready"}
        self.steps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_answer_only_task_does_not_get_forced_to_act(self):
        async def chat(prompt, model, new):
            return json.dumps({"type": "final",
                               "content": "## 结论\n\n- ok\n\n## 风险\n\n- none"},
                              ensure_ascii=False)

        runner = AgentRunner(
            ToolRegistry(self.tmp, {}), chat, {"max_total_steps": 5},
            state_getter=lambda: dict(self.state),
            state_saver=lambda **kw: self.state.update(kw),
            step_saver=lambda s: self.steps.append(s),
            file_event_saver=lambda e: None)
        result = asyncio.run(runner.run(
            "用结构化 Markdown 返回两节报告，不要创建文件。", "m"))
        self.assertIn("## 结论", result.content)
        self.assertFalse(any(s.get("status") == "noop_guard" for s in self.steps))

    def test_positive_action_still_requires_action(self):
        self.assertTrue(AgentRunner._has_positive_action(
            "创建 out.txt", ("创建", "写")))
        self.assertFalse(AgentRunner._has_positive_action(
            "不要修改文件", ("创建", "修改", "写")))


if __name__ == "__main__":
    unittest.main()
