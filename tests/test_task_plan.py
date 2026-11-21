# -*- coding: utf-8 -*-
"""Task 模型、依赖图和 SQLite 持久化测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.task_plan import (TaskPlanError, TaskStore, downstream_ids,
                              normalize_tasks, plan_progress, ready_ids,
                              recompute_statuses, validate_tasks)


class GraphTests(unittest.TestCase):
    def test_root_ready_and_dependent_pending(self):
        tasks = normalize_tasks([
            {"id": "scan", "title": "扫描项目"},
            {"id": "fix", "title": "修复问题", "depends_on": ["scan"]},
            {"id": "report", "title": "写报告", "depends_on": ["fix"]},
        ])
        self.assertEqual([t["status"] for t in tasks], ["ready", "pending", "pending"])
        self.assertEqual(ready_ids(tasks), ["scan"])

    def test_cycle_rejected(self):
        with self.assertRaises(TaskPlanError):
            normalize_tasks([
                {"id": "a", "title": "A", "depends_on": ["b"]},
                {"id": "b", "title": "B", "depends_on": ["a"]},
            ])

    def test_missing_dependency_rejected(self):
        with self.assertRaises(TaskPlanError):
            normalize_tasks([{"id": "a", "title": "A", "depends_on": ["missing"]}])

    def test_ready_after_dependency_completed(self):
        tasks = normalize_tasks([
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B", "depends_on": ["a"]},
        ])
        tasks[0]["status"] = "completed"
        recompute_statuses(tasks)
        self.assertEqual(tasks[1]["status"], "ready")
        self.assertEqual(ready_ids(tasks), ["b"])

    def test_failure_blocks_downstream(self):
        tasks = normalize_tasks([
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B", "depends_on": ["a"]},
            {"id": "c", "title": "C", "depends_on": ["b"]},
        ])
        tasks[0]["status"] = "failed"
        recompute_statuses(tasks)
        self.assertEqual(tasks[1]["status"], "blocked")
        self.assertEqual(tasks[2]["status"], "blocked")
        self.assertEqual(downstream_ids(tasks, ["a"]), ["b", "c"])

    def test_priority_controls_ready_order(self):
        tasks = normalize_tasks([
            {"id": "low", "title": "Low", "priority": 0},
            {"id": "high", "title": "High", "priority": 5},
        ])
        self.assertEqual(ready_ids(tasks), ["high", "low"])

    def test_acceptance_and_outputs_preserved(self):
        tasks = normalize_tasks([{
            "id": "a", "title": "A",
            "outputs": [{"path": "report.md"}],
            "acceptance": ["report.md 存在"],
            "input": {"scope": "src"},
        }])
        self.assertEqual(tasks[0]["outputs"][0]["path"], "report.md")
        self.assertEqual(tasks[0]["acceptance"], ["report.md 存在"])
        self.assertEqual(tasks[0]["input"], {"scope": "src"})


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TaskStore(Path(self.tmp.name) / "tasks.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _make(self):
        return self.store.create_plan("s1", [
            {"id": "scan", "title": "扫描"},
            {"id": "fix", "title": "修复", "depends_on": ["scan"],
             "acceptance": ["tests pass"]},
        ], goal="修复项目")

    def test_create_and_reload(self):
        plan = self._make()
        self.assertEqual(plan["plan_id"][:5], "plan-")
        self.assertEqual(plan["progress"]["total"], 2)
        again = TaskStore(Path(self.tmp.name) / "tasks.db").get_plan("s1")
        self.assertIsNotNone(again)
        self.assertEqual([t["id"] for t in again["tasks"]], ["scan", "fix"])
        self.assertEqual(again["ready"], ["scan"])

    def test_dependency_guard(self):
        self._make()
        with self.assertRaises(TaskPlanError):
            self.store.update_task("s1", "fix", status="running")
        self.store.update_task("s1", "scan", status="completed")
        plan = self.store.update_task("s1", "fix", status="running",
                                      outputs=[{"path": "fixed.txt"}])
        self.assertEqual(plan["tasks"][1]["status"], "running")
        self.assertEqual(plan["tasks"][1]["outputs"][0]["path"], "fixed.txt")

    def test_failed_blocks_downstream_and_resume(self):
        self._make()
        plan = self.store.update_task("s1", "scan", status="failed",
                                      failure_reason="扫描失败")
        self.assertEqual(plan["tasks"][0]["retry_count"], 1)
        self.assertEqual(plan["tasks"][1]["status"], "blocked")
        plan = self.store.resume("s1", task_id="scan")
        self.assertEqual(plan["tasks"][0]["status"], "ready")
        self.assertEqual(plan["tasks"][1]["status"], "pending")

    def test_cancel_and_resume_plan(self):
        self._make()
        plan = self.store.cancel("s1")
        self.assertEqual(plan["status"], "cancelled")
        self.assertTrue(all(t["status"] == "cancelled" for t in plan["tasks"]))
        plan = self.store.resume("s1")
        self.assertEqual(plan["status"], "active")
        self.assertEqual(plan["tasks"][0]["status"], "ready")

    def test_replan_adds_dependency_and_reports_affected(self):
        self._make()
        plan = self.store.replan("s1", [
            {"task_id": "fix", "depends_on": ["scan"]},
            {"task_id": "verify", "title": "验证", "depends_on": ["fix"]},
        ])
        self.assertEqual([t["id"] for t in plan["tasks"]], ["scan", "fix", "verify"])
        self.assertEqual(plan["revision"], 2)
        self.assertIn("fix", plan["affected"])
        self.assertIn("verify", plan["affected"])

    def test_replan_cannot_remove_dependency_target(self):
        self._make()
        with self.assertRaises(TaskPlanError):
            self.store.replan("s1", [{"task_id": "scan", "remove": True}])

    def test_no_second_active_plan_without_replace(self):
        self._make()
        with self.assertRaises(TaskPlanError):
            self.store.create_plan("s1", [{"id": "x", "title": "X"}])

    def test_plan_completes_when_tasks_done(self):
        self._make()
        self.store.update_task("s1", "scan", status="completed")
        plan = self.store.update_task("s1", "fix", status="completed")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan_progress(plan["tasks"])["completed"], 2)


if __name__ == "__main__":
    unittest.main()
