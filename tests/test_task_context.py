# -*- coding: utf-8 -*-
"""任务图摘要必须进入会话持久化与压缩上下文。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.agent import AgentRunner
from gantry.session_store import SessionStore
from gantry.tools import ToolRegistry


class TaskContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="task_ctx_"))
        self.store = SessionStore(self.tmp / "agent.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_task_summary_persists(self):
        self.store.create("s1", "t", str(self.tmp), "m")
        summary = {"plan_id": "p1", "status": "active", "ready": ["a"],
                   "progress": {"total": 2, "completed": 1}}
        self.store.patch("s1", task_summary=summary)
        got = self.store.get("s1")
        self.assertEqual(got["task_summary"], summary)

    def test_agent_summary_keeps_task_plan(self):
        reg = ToolRegistry(self.tmp, {})
        state = {"task_summary": {"plan_id": "p1", "ready": ["a"],
                                  "progress": {"total": 1, "completed": 0}}}
        runner = AgentRunner(reg, lambda *a, **k: "", {}, state_getter=lambda: state,
                             state_saver=lambda **kw: None,
                             step_saver=lambda *a, **k: None,
                             file_event_saver=lambda *a, **k: None)
        summary = runner._deterministic_summary("任务", [], state)
        self.assertEqual(summary["task_plan"]["plan_id"], "p1")
        ledger = runner._work_ledger(state, [])
        self.assertIn("任务图", ledger)


if __name__ == "__main__":
    unittest.main()
