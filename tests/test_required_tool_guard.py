# -*- coding: utf-8 -*-
"""明确要求 task_run 时，禁止只说不做。"""
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


class RequiredToolGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="required_tool_"))
        self.state = {"state": "ready"}
        self.steps = []
        self.reg = ToolRegistry(self.tmp, {})
        self.calls = 0

        async def task_run():
            self.calls += 1
            return {"completed": 1, "failed": 0, "results": []}

        self.reg.register("task_run", task_run,
                          "execute task graph", {}, approval=False)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runner(self, actions):
        async def chat(prompt, model, new):
            return json.dumps(actions.pop(0), ensure_ascii=False)

        return AgentRunner(
            self.reg, chat, {"max_total_steps": 10},
            state_getter=lambda: dict(self.state),
            state_saver=lambda **kw: self.state.update(kw),
            step_saver=lambda s: self.steps.append(s),
            file_event_saver=lambda e: None)

    def test_final_without_task_run_is_blocked_then_executed(self):
        actions = [
            {"type": "final", "content": "我打算执行 task_run"},
            {"type": "tool", "tool": "task_run", "arguments": {}},
            {"type": "final", "content": "执行完成"},
            {"type": "final", "content": "执行完成"},
        ]
        result = asyncio.run(self._runner(actions).run(
            "请调用 task_run 执行任务图", "m"))
        self.assertEqual(result.content, "执行完成")
        self.assertEqual(self.calls, 1)
        self.assertTrue(any(s.get("status") == "required_tool_guard" for s in self.steps))

    def test_never_executing_fails(self):
        actions = [{"type": "final", "content": "只给步骤"} for _ in range(5)]
        result = asyncio.run(self._runner(actions).run(
            "请调用 task_run 执行任务图", "m"))
        self.assertIn("task_run", result.content)
        self.assertEqual(self.calls, 0)
        self.assertTrue(any(s.get("status") == "required_tool_blocked" for s in self.steps))


if __name__ == "__main__":
    unittest.main()
