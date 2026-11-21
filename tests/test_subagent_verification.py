# -*- coding: utf-8 -*-
"""子 Agent 必须把真实验证证据带回父 Agent。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import bridge  # noqa: F401  initialize bridge before subagents
from gantry.subagents import _verification_from_steps


class SubagentVerificationTests(unittest.TestCase):
    def test_only_passed_validation_counts(self):
        steps = [
            {"tool": "validate_python", "status": "ok", "result": {"passed": True}},
            {"tool": "run_unittest", "status": "ok", "result": {"passed": False}},
        ]
        self.assertEqual(_verification_from_steps(steps),
                         {"syntax_passed": True, "tests_passed": False})

    def test_claimed_text_does_not_count(self):
        steps = [{"tool": "validate_python", "status": "error",
                  "result": {"passed": True}}]
        self.assertEqual(_verification_from_steps(steps),
                         {"syntax_passed": False, "tests_passed": False})


if __name__ == "__main__":
    unittest.main()
