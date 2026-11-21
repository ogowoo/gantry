# -*- coding: utf-8 -*-
"""Batch 3 验收引擎：文件/格式/命令/测试/组合/独立验证。"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.task_verify import independent_review, review_task


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outputs_and_combined_checks(self):
        (self.root / "report.md").write_text("hello report", encoding="utf-8")
        task = {
            "outputs": [{"path": "report.md", "contains": "hello"}],
            "acceptance": [
                {"type": "all_of", "checks": [
                    {"type": "file_exists", "path": "report.md"},
                    {"type": "file_contains", "path": "report.md", "text": "report"},
                ]},
                {"type": "final_contains", "text": "完成"},
            ],
        }
        v = review_task(task, {"content": "完成"}, self.root)
        self.assertTrue(v["ok"])
        self.assertTrue(v["verified"])

    def test_format_json(self):
        (self.root / "x.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        v = review_task({"acceptance": [{"type": "format", "path": "x.json",
                                         "format": "json"}]},
                        {"content": "ok"}, self.root)
        self.assertTrue(v["ok"])
        self.assertTrue(v["verified"])

    def test_command_requires_explicit_enable(self):
        task = {"acceptance": [{"type": "command",
                                "command": f'"{sys.executable}" -c "print(123)"',
                                "exit_code": 0, "stdout_contains": "123"}]}
        blocked = review_task(task, {"content": "claimed"}, self.root)
        self.assertFalse(blocked["ok"])
        passed = review_task(task, {"content": "claimed"}, self.root,
                             allow_commands=True)
        self.assertTrue(passed["ok"])
        self.assertTrue(passed["verified"])

    def test_unittest_condition(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8")
        task = {"acceptance": [{"type": "tests", "path": "tests"}]}
        v = review_task(task, {"content": "ok"}, self.root, allow_commands=True)
        self.assertTrue(v["ok"])

    def test_any_of_and_not(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        task = {"acceptance": [
            {"type": "any_of", "checks": [
                {"type": "file_exists", "path": "missing"},
                {"type": "file_exists", "path": "a.txt"},
            ]},
            {"type": "not", "check": {"type": "file_exists", "path": "missing"}},
        ]}
        v = review_task(task, {"content": "ok"}, self.root)
        self.assertTrue(v["ok"])

    def test_high_risk_requires_deterministic_evidence(self):
        task = {"risk": "high", "acceptance": ["looks good"]}
        v = review_task(task, {"content": "done"}, self.root)
        self.assertFalse(v["ok"])
        self.assertTrue(v["high_risk"])

    def test_independent_reviewer_requires_pass(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        task = {"acceptance": [{"type": "file_exists", "path": "a.txt"}]}

        async def reviewer(prompt, description, model, steps):
            self.assertIn("独立验证员", prompt)
            return {"content": '{"verdict":"pass","issues":[],"reason":"ok"}'}

        v = asyncio.run(independent_review(task, {"content": "done"}, self.root, reviewer))
        self.assertTrue(v["ok"])
        self.assertTrue(v["independent"]["ok"])

    def test_independent_reviewer_cannot_override_failure(self):
        task = {"outputs": [{"path": "missing.txt"}]}

        async def reviewer(prompt, description, model, steps):
            return {"content": '{"verdict":"pass"}'}

        v = asyncio.run(independent_review(task, {"content": "done"}, self.root, reviewer))
        self.assertFalse(v["ok"])


if __name__ == "__main__":
    unittest.main()
