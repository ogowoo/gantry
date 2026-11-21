# -*- coding: utf-8 -*-
"""评测指标与 Benchmark 固定集的结构测试。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval import compute_metrics  # noqa: E402


class MetricsTests(unittest.TestCase):
    def test_metrics_from_results(self):
        results = [
            {"ok": True, "seconds": 2, "steps": 3, "tools": ["a", "b"],
             "attempts": 0, "checks": [{"ok": True}, {"ok": True}],
             "subagent_calls": 2, "subagent_ok": 1, "statuses": ["ok"],
             "usage": {"total_tokens": 100}},
            {"ok": False, "seconds": 4, "steps": 5, "tools": ["a"],
             "attempts": 1, "checks": [{"ok": False}],
             "subagent_calls": 1, "subagent_ok": 0,
             "statuses": ["no_progress"], "usage": {"total_tokens": 50}},
        ]
        m = compute_metrics(results)
        self.assertEqual(m["tasks"], 2)
        self.assertEqual(m["completion_rate"], 0.5)
        self.assertEqual(m["acceptance_passed"], 2)
        self.assertEqual(m["acceptance_rate"], round(2 / 3, 4))
        self.assertEqual(m["avg_seconds"], 3.0)
        self.assertEqual(m["avg_steps"], 4.0)
        self.assertEqual(m["avg_retries"], 0.5)
        self.assertEqual(m["no_progress"], 1)
        self.assertEqual(m["subagent_success_rate"], round(1 / 3, 4))
        self.assertEqual(m["total_tokens"], 150)


class BenchmarkSuiteTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "tests" / "evals" / "tasks.json").read_text(encoding="utf-8"))
        self.tasks = self.data["tasks"]

    def test_at_least_30_unique_tasks(self):
        self.assertGreaterEqual(len(self.tasks), 30)
        ids = [t["id"] for t in self.tasks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_required_categories_present(self):
        cats = {t.get("category") for t in self.tasks}
        required = {
            "simple-tool", "multi-step-file-ops", "multi-file-code-change",
            "complex-debug", "project-analysis", "report", "web-search",
            "long-context", "compression", "failure-recovery", "multi-agent",
            "multi-agent-parallel", "security-boundary", "planning",
            "ambiguous-planning",
        }
        self.assertEqual(sorted(required - cats), [])

    def test_every_task_has_assertions(self):
        for task in self.tasks:
            self.assertTrue(task.get("assert"), task["id"])
            self.assertTrue(task.get("task"), task["id"])


if __name__ == "__main__":
    unittest.main()
