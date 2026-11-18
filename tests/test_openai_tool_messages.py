# -*- coding: utf-8 -*-
"""OpenAI 兼容性：DSH / Agent 客户端会把工具回合也发进来。

真实 422（DSH 接 Gantry 时）：
  messages[3].role = "tool"            → 只允许 system/user/assistant
  messages[4].content = null           → 必须是字符串
工具调用的历史不该让整个请求 422；Copilot 网页不执行工具，但正文要能取出来。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import bridge as b  # noqa: E402


class MessageSchemaTests(unittest.TestCase):
    def test_tool_role_and_null_content_accepted(self):
        req = b.AgentRequest(model="copilot-auto", messages=[
            {"role": "user", "content": "帮我查一下"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "t1", "type": "function",
                             "function": {"name": "search", "arguments": "{}"}}]},
            {"role": "tool", "content": None, "tool_call_id": "t1"},
            {"role": "tool", "content": "查询结果"},
        ])
        self.assertEqual([m.role for m in req.messages],
                         ["user", "assistant", "tool", "tool"])

    def test_function_and_developer_roles(self):
        req = b.Request(messages=[
            {"role": "developer", "content": "规则"},
            {"role": "function", "content": "结果"},
            {"role": "user", "content": "问题"},
        ])
        self.assertEqual(len(req.messages), 3)

    def test_multimodal_content_flattened(self):
        m = b.Message(role="user", content=[
            {"type": "text", "text": "第一段"},
            {"type": "text", "text": "第二段"},
        ])
        self.assertEqual(b.message_text(m), "第一段\n第二段")

    def test_null_tool_content_is_empty_text(self):
        m = b.Message(role="tool", content=None, tool_call_id="t1")
        self.assertEqual(b.message_text(m), "")
