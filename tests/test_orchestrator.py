# -*- coding: utf-8 -*-
"""Batch 2 编排：依赖 wave、并发、冲突、超时、返工和 main 任务。"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.orchestrator import run_plan
from gantry.task_plan import TaskStore


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = TaskStore(self.root / "tasks.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self, tasks):
        return self.store.create_plan("s1", tasks, goal="test")

    def _worker(self, files=None, errors=None, delay=0.0, counts=None):
        files = files or {}
        errors = errors or {}
        counts = counts if counts is not None else {}

        async def run(prompt, description, model, max_steps):
            tid = description.split(":")[-1]
            counts[tid] = counts.get(tid, 0) + 1
            if delay:
                await asyncio.sleep(delay)
            if tid in errors:
                return {"error": errors[tid], "content": ""}
            targets = files.get(tid) or {}
            for rel, content in targets.items():
                path = self.root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return {"agent_id": "sub-" + tid, "content": f"done {tid}", "error": None}

        return run

    def test_dependency_order(self):
        self._plan([
            {"id": "scan", "title": "扫描", "executor": "subagent",
             "outputs": [{"path": "scan.txt"}]},
            {"id": "fix", "title": "修复", "executor": "subagent",
             "depends_on": ["scan"], "outputs": [{"path": "fix.txt"}]},
        ])
        out = asyncio.run(run_plan(
            self.store, "s1", self._worker({"scan": {"scan.txt": "s"},
                                           "fix": {"fix.txt": "f"}}),
            workspace=self.root))
        self.assertEqual(out["completed"], 2)
        plan = self.store.get_plan("s1")
        self.assertEqual(plan["status"], "completed")
        self.assertEqual([t["status"] for t in plan["tasks"]], ["completed", "completed"])

    def test_parallel_cap_observed(self):
        self._plan([
            {"id": "a", "title": "A", "executor": "subagent",
             "outputs": [{"path": "a.txt"}]},
            {"id": "b", "title": "B", "executor": "subagent",
             "outputs": [{"path": "b.txt"}]},
        ])
        active = {"now": 0, "max": 0}

        async def run(prompt, description, model, max_steps):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.06)
            tid = description.split(":")[-1]
            (self.root / f"{tid}.txt").write_text(tid, encoding="utf-8")
            active["now"] -= 1
            return {"content": "ok"}

        out = asyncio.run(run_plan(self.store, "s1", run, max_concurrency=2,
                                   workspace=self.root))
        self.assertEqual(out["completed"], 2)
        self.assertEqual(active["max"], 2)

    def test_failure_blocks_downstream(self):
        self._plan([
            {"id": "a", "title": "A", "executor": "subagent"},
            {"id": "b", "title": "B", "executor": "subagent", "depends_on": ["a"]},
        ])
        counts = {}
        out = asyncio.run(run_plan(
            self.store, "s1", self._worker(errors={"a": "boom"}, counts=counts),
            workspace=self.root))
        plan = self.store.get_plan("s1")
        self.assertEqual(plan["tasks"][0]["status"], "failed")
        self.assertEqual(plan["tasks"][1]["status"], "blocked")
        self.assertEqual(counts, {"a": 1})
        self.assertEqual(out["failed"], 1)

    def test_permission_error_is_not_retried(self):
        self._plan([{"id": "a", "title": "A", "executor": "subagent",
                     "max_retries": 3}])
        counts = {"a": 0}

        async def run(prompt, description, model, max_steps):
            counts["a"] += 1
            return {"error": "permission denied: sandbox", "content": ""}

        out = asyncio.run(run_plan(self.store, "s1", run, workspace=self.root))
        self.assertEqual(counts["a"], 1)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["results"][0]["error_kind"], "permission")

    def test_rework_after_review_failure(self):
        self._plan([
            {"id": "a", "title": "A", "executor": "subagent", "max_retries": 1,
             "outputs": [{"path": "a.txt"}]},
        ])
        counts = {"a": 0}

        async def run(prompt, description, model, max_steps):
            counts["a"] += 1
            if counts["a"] == 1:
                return {"content": "claimed done", "error": None}
            (self.root / "a.txt").write_text("real", encoding="utf-8")
            return {"content": "done", "error": None}

        out = asyncio.run(run_plan(self.store, "s1", run, workspace=self.root))
        plan = self.store.get_plan("s1")
        self.assertEqual(plan["tasks"][0]["status"], "completed")
        self.assertEqual(plan["tasks"][0]["retry_count"], 1)
        self.assertEqual(counts["a"], 2)
        self.assertGreaterEqual(out["retrying"], 1)

    def test_timeout_fails_task(self):
        self._plan([{"id": "slow", "title": "Slow", "executor": "subagent"}])

        async def run(prompt, description, model, max_steps):
            await asyncio.sleep(0.2)
            return {"content": "late"}

        out = asyncio.run(run_plan(self.store, "s1", run, timeout_seconds=0.05,
                                   workspace=self.root))
        self.assertEqual(out["failed"], 1)
        self.assertEqual(self.store.get_plan("s1")["tasks"][0]["status"], "failed")

    def test_same_output_path_is_not_parallel_and_second_fails(self):
        self._plan([
            {"id": "a", "title": "A", "executor": "subagent",
             "outputs": [{"path": "same.txt"}]},
            {"id": "b", "title": "B", "executor": "subagent",
             "outputs": [{"path": "same.txt"}]},
        ])
        active = {"now": 0, "max": 0}

        async def run(prompt, description, model, max_steps):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.02)
            (self.root / "same.txt").write_text(description, encoding="utf-8")
            active["now"] -= 1
            return {"content": "ok"}

        out = asyncio.run(run_plan(self.store, "s1", run, max_concurrency=2,
                                   workspace=self.root))
        self.assertEqual(active["max"], 1)
        self.assertEqual(out["completed"], 1)
        self.assertEqual(out["failed"], 1)

    def test_main_task_is_left_for_main_agent(self):
        self._plan([{"id": "main", "title": "Main", "executor": "main"}])
        out = asyncio.run(run_plan(self.store, "s1", self._worker()))
        self.assertEqual(out["needs_main"], ["main"])
        self.assertEqual(self.store.get_plan("s1")["tasks"][0]["status"], "ready")

    def test_per_task_model_is_used(self):
        self._plan([{"id": "a", "title": "A", "executor": "subagent",
                     "model": "model-a"}])
        seen = []

        async def run(prompt, description, model, max_steps):
            seen.append(model)
            return {"content": "ok"}

        asyncio.run(run_plan(self.store, "s1", run, model="model-default"))
        self.assertEqual(seen, ["model-a"])


if __name__ == "__main__":
    unittest.main()
