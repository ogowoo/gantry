# -*- coding: utf-8 -*-
"""任务产物审查的最小确定性测试。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.task_verify import review_task


class TaskVerifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_output_exists_and_content(self):
        (self.root / "report.md").write_text("hello report", encoding="utf-8")
        task = {"outputs": [{"path": "report.md", "contains": "hello"}],
                "acceptance": [{"type": "file_exists", "path": "report.md"},
                               {"type": "final_contains", "text": "完成"}]}
        v = review_task(task, {"content": "完成"}, self.root)
        self.assertTrue(v["ok"])
        self.assertTrue(v["verified"])

    def test_missing_output_fails(self):
        task = {"outputs": [{"path": "missing.md"}]}
        v = review_task(task, {"content": "done"}, self.root)
        self.assertFalse(v["ok"])
        self.assertIn("output:missing.md", v["failed"])

    def test_path_traversal_rejected(self):
        task = {"outputs": [{"path": "../outside.txt"}]}
        v = review_task(task, {"content": "done"}, self.root)
        self.assertFalse(v["ok"])

    def test_no_machine_checks_is_unverified_but_not_failure(self):
        v = review_task({"outputs": [], "acceptance": []},
                        {"content": "a textual answer"}, self.root)
        self.assertTrue(v["ok"])
        self.assertFalse(v["verified"])

    def test_manual_acceptance_is_not_claimed_verified(self):
        v = review_task({"acceptance": ["报告看起来完整"]},
                        {"content": "done"}, self.root)
        self.assertTrue(v["ok"])
        self.assertFalse(v["verified"])

    def test_child_error_fails(self):
        v = review_task({}, {"error": "subagent timeout"}, self.root)
        self.assertFalse(v["ok"])
        self.assertEqual(v["checks"][0]["name"], "agent_error")

    def test_min_bytes_and_file_contains(self):
        (self.root / "a.txt").write_text("x" * 20, encoding="utf-8")
        task = {"acceptance": [
            {"type": "file_min_bytes", "path": "a.txt", "bytes": 10},
            {"type": "file_contains", "path": "a.txt", "text": "xxx"},
        ]}
        v = review_task(task, {"content": "ok"}, self.root)
        self.assertTrue(v["ok"])
        self.assertTrue(v["verified"])

    def test_child_test_evidence_is_checked(self):
        task = {"acceptance": [{"type": "tests_pass"}]}
        ok = review_task(task, {"content": "ok",
                                "verification": {"tests_passed": True}}, self.root)
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["verified"])
        bad = review_task(task, {"content": "claimed passed"}, self.root)
        self.assertFalse(bad["ok"])


if __name__ == "__main__":
    unittest.main()
