# -*- coding: utf-8 -*-
"""Batch 6 安全风险分级、审批建议和不可信内容规则。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.security import (audit_config, classify_tool_risk, redact_secrets,
                             requires_approval_for)
from gantry.tools import ToolRegistry


class SecurityPolicyTests(unittest.TestCase):
    def test_risk_levels(self):
        self.assertEqual(classify_tool_risk("read_text"), "read")
        self.assertEqual(classify_tool_risk("write_text"), "workspace_write")
        self.assertEqual(classify_tool_risk("write_text", {"overwrite": True}), "destructive")
        self.assertEqual(classify_tool_risk("delete_path"), "destructive")
        self.assertEqual(classify_tool_risk("outlook_send"), "external_irreversible")
        self.assertEqual(classify_tool_risk("pwsh"), "external_irreversible")

    def test_approval_for_overwrite_and_move(self):
        self.assertTrue(requires_approval_for("write_text", {"overwrite": True}))
        self.assertTrue(requires_approval_for("move_path", {}))
        self.assertTrue(requires_approval_for("delete_path", {}))
        self.assertTrue(requires_approval_for("pwsh", {}))
        self.assertFalse(requires_approval_for("read_text", {}))
        self.assertFalse(requires_approval_for("write_text", {"overwrite": False}))

    def test_registry_parameter_approval(self):
        with tempfile.TemporaryDirectory() as d:
            reg = ToolRegistry(Path(d), {})
            self.assertTrue(reg.requires_approval_for("write_text", {"overwrite": True}))
            self.assertFalse(reg.requires_approval_for("read_text", {}))

    def test_redact_secrets(self):
        out = redact_secrets("Authorization: Bearer abcdefghijklmnop api_key=secret123")
        self.assertNotIn("secret123", out)
        self.assertIn("***", out)

    def test_audit_config_finds_plaintext_keys(self):
        findings = audit_config({"api_token": "abc",
                                 "providers": {"x": {"api_key": "plain"}}})
        paths = {f["path"] for f in findings}
        self.assertIn("api_token", paths)
        self.assertIn("providers.x.api_key", paths)
        self.assertEqual(audit_config({"api_token": "${TOKEN}",
                                       "providers": {"x": {"api_key": "${KEY}"}}}), [])


if __name__ == "__main__":
    unittest.main()
