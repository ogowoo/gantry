# -*- coding: utf-8 -*-
"""统一错误分类与重试决策。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.error_taxonomy import classify_exception, classify_text, retry_decision


class ErrorTaxonomyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify_text("Connection reset by peer").kind, "network")
        self.assertEqual(classify_text("HTTP 429 rate limit").kind, "rate_limit")
        self.assertEqual(classify_text("Permission denied: path outside workspace").kind,
                         "permission")
        self.assertEqual(classify_text("invalid argument: missing path").kind, "argument")
        self.assertEqual(classify_text("timed out after 30s").kind, "timeout")

    def test_exception_mapping(self):
        self.assertEqual(classify_exception(TimeoutError("timed out")).kind, "timeout")
        self.assertEqual(classify_exception(PermissionError("access denied")).kind,
                         "permission")
        self.assertEqual(classify_exception(ValueError("bad argument")).kind, "argument")

    def test_retry_decisions(self):
        info = classify_text("connection reset")
        self.assertEqual(retry_decision(info, 0, 1)["action"], "retry")
        self.assertEqual(retry_decision(info, 1, 1)["action"], "stop")
        perm = classify_text("permission denied")
        self.assertEqual(retry_decision(perm, 0, 3)["retry"], False)
        arg = classify_text("invalid argument")
        self.assertEqual(retry_decision(arg, 0, 3)["action"], "fix")


if __name__ == "__main__":
    unittest.main()
