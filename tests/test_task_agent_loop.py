# -*- coding: utf-8 -*-
"""AgentRunner 真实工具循环中的 task_plan/task_update 集成。"""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.agent import AgentRunner
from gantry.task_plan import TaskStore
from gantry.tools import ToolRegistry
from gantry.tools_extra import register_extra_tools


class TaskAgentLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="task_loop_"))
        self.state = {"state": "ready"}
        self.steps = []
        self.reg = ToolRegistry(self.tmp, {})
        self.reg.state_saver = lambda **kw: self.state.update(kw)
        self.store = TaskStore(self.tmp / "tasks.db")
        register_extra_tools(
            self.reg,
            {"prompt_language": "zh", "task_db_path": str(self.tmp / "tasks.db"),
             "tools_extra": {"plan": True, "search": False, "web": False,
                             "shell": False, "vision": False, "ask": False,
                             "team": False, "goal": False}},
            session_getter=lambda: "s1", task_store=self.store)
        self.actions = [
            {"type": "tool", "tool": "task_plan", "arguments": {
                "goal": "分析并修复",
                "tasks": [{"id": "scan", "title": "扫描"},
                          {"id": "fix", "title": "修复", "depends_on": ["scan"]}]}},
            {"type": "tool", "tool": "task_update", "arguments": {
                "task_id": "scan", "status": "completed"}},
            {"type": "tool", "tool": "task_update", "arguments": {
                "task_id": "fix", "status": "completed",
                "outputs": [{"path": "report.md"}]}},
            {"type": "final", "content": "## 完成\n- 已处理"},
        ]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_loop_creates_and_completes_plan(self):
        async def chat(prompt, model, make_new):
            return json.dumps(self.actions.pop(0), ensure_ascii=False)

        runner = AgentRunner(
            self.reg, chat, {"max_total_steps": 10, "prompt_language": "zh"},
            state_getter=lambda: dict(self.state),
            state_saver=lambda **kw: self.state.update(kw),
            step_saver=lambda s: self.steps.append(s),
            file_event_saver=lambda e: None)
        result = asyncio.run(runner.run("分析并修复项目", "m"))
        self.assertIn("完成", result.content)
        self.assertEqual(len(self.actions), 0)
        plan = self.store.get_plan("s1")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["progress"]["completed"], 2)
        self.assertTrue(any(s.get("tool") == "task_plan" for s in self.steps))
        self.assertEqual(self.state["todos"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
