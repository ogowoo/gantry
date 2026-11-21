# -*- coding: utf-8 -*-
"""任务图提示词只在 task_plan 工具真正可用时注入。"""
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


class TaskPromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="task_prompt_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prompt(self, plan_enabled: bool) -> str:
        reg = ToolRegistry(self.tmp, {})
        store = TaskStore(self.tmp / "tasks.db")
        register_extra_tools(
            reg,
            {"prompt_language": "zh",
             "tools_extra": {"plan": plan_enabled, "search": False, "web": False,
                             "shell": False, "vision": False, "ask": False,
                             "team": False, "goal": False}},
            session_getter=lambda: "s1", task_store=store)
        runner = AgentRunner(reg, lambda *a, **k: "", {"prompt_language": "zh"},
                             state_getter=lambda: {}, state_saver=lambda **kw: None,
                             step_saver=lambda *a, **k: None,
                             file_event_saver=lambda *a, **k: None)
        return runner._system_prompt()

    def test_task_plan_prompt_present_when_enabled(self):
        prompt = self._prompt(True)
        self.assertIn("任务图（复杂任务）", prompt)
        self.assertIn("task_plan", prompt)
        self.assertIn("task_replan", prompt)

    def test_task_plan_prompt_absent_when_disabled(self):
        prompt = self._prompt(False)
        self.assertNotIn("任务图（复杂任务）", prompt)
        self.assertNotIn("task_plan", prompt)


if __name__ == "__main__":
    unittest.main()
