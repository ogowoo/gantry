# -*- coding: utf-8 -*-
"""扩展工具层：task_* 工具与 todo 投影。"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.task_plan import TaskStore
from gantry.tools import ToolRegistry
from gantry.tools_extra import ExtraToolError, register_extra_tools


class TaskToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state = {}
        self.reg = ToolRegistry(root, {})
        self.reg.state_saver = lambda **kw: self.state.update(kw)
        self.store = TaskStore(root / "tasks.db")
        self.impl = register_extra_tools(
            self.reg,
            {"tools_extra": {"plan": True, "search": False, "web": False,
                             "shell": False, "vision": False, "ask": False,
                             "team": False, "goal": False}},
            session_getter=lambda: "s1", task_store=self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_registered(self):
        names = {x["name"] for x in self.reg.descriptions()}
        for name in ("task_plan", "task_read", "task_update", "task_cancel",
                     "task_resume", "task_replan"):
            self.assertIn(name, names)

    def test_plan_and_todo_projection(self):
        plan = asyncio.run(self.impl.task_plan(
            "修复项目", [{"id": "scan", "title": "扫描"},
                         {"id": "fix", "title": "修复", "depends_on": ["scan"]}]))
        self.assertEqual(plan["ready"], ["scan"])
        self.assertEqual(self.state["todos"][0]["content"], "[scan] 扫描")
        self.assertEqual(len(self.state["todos"]), 2)
        self.assertEqual(self.state["task_summary"]["progress"]["total"], 2)
        self.assertEqual(self.state["task_summary"]["ready"], ["scan"])

    def test_update_and_read(self):
        asyncio.run(self.impl.task_plan("g", [{"id": "a", "title": "A"}]))
        plan = asyncio.run(self.impl.task_update("a", status="completed",
                                                 outputs=[{"path": "done.txt"}]))
        self.assertEqual(plan["tasks"][0]["status"], "completed")
        read = asyncio.run(self.impl.task_read())
        self.assertEqual(read["progress"]["completed"], 1)

    def test_bad_dependency_is_tool_error(self):
        with self.assertRaises(ExtraToolError):
            asyncio.run(self.impl.task_plan("g", [
                {"id": "a", "title": "A", "depends_on": ["missing"]}]))

    def test_cancel_and_resume(self):
        asyncio.run(self.impl.task_plan("g", [{"id": "a", "title": "A"}]))
        cancelled = asyncio.run(self.impl.task_cancel())
        self.assertEqual(cancelled["status"], "cancelled")
        resumed = asyncio.run(self.impl.task_resume())
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(resumed["tasks"][0]["status"], "ready")

    def test_recover_running_task(self):
        asyncio.run(self.impl.task_plan("g", [{"id": "a", "title": "A"}]))
        asyncio.run(self.impl.task_update("a", status="running"))
        recovered = asyncio.run(self.impl.task_recover())
        self.assertIn("a", recovered["recovered"])
        self.assertEqual(recovered["tasks"][0]["status"], "ready")

    def test_replan(self):
        asyncio.run(self.impl.task_plan("g", [{"id": "a", "title": "A"}]))
        plan = asyncio.run(self.impl.task_replan(
            [{"task_id": "b", "title": "B", "depends_on": ["a"]}]))
        self.assertEqual([t["id"] for t in plan["tasks"]], ["a", "b"])

    def test_task_run_dispatches_and_reviews_subagent(self):
        root = Path(self.tmp.name)

        async def fake(task, description, model, max_steps):
            (root / "out.txt").write_text("artifact", encoding="utf-8")
            return {"content": "done", "agent_id": "sub-1"}

        self.impl.subagent_fn = fake
        asyncio.run(self.impl.task_plan("g", [{
            "id": "a", "title": "A", "executor": "subagent",
            "outputs": [{"path": "out.txt"}],
            "max_retries": 0,
        }]))
        out = asyncio.run(self.impl.task_run())
        self.assertEqual(out["completed"], 1)
        self.assertEqual(out["verified"], 1)
        self.assertTrue((root / "out.txt").is_file())


if __name__ == "__main__":
    unittest.main()
