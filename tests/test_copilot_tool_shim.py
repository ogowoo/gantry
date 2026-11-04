# -*- coding: utf-8 -*-
"""Copilot 网页模型的 tool_calls 合成层（提示词 + JSON 解析）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import bridge as b  # noqa: E402
from gantry import copilot_tools  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_simple_shape(self):
        d = copilot_tools.parse_decision(
            '{"tool_calls":[{"name":"weather","arguments":{"city":"北京"}}]}')
        self.assertEqual(d["content"], None)
        self.assertEqual(d["tool_calls"][0]["function"]["name"], "weather")
        self.assertEqual(d["tool_calls"][0]["function"]["arguments"],
                         '{"city": "北京"}')

    def test_openai_shape_with_string_args(self):
        d = copilot_tools.parse_decision(
            '{"tool_calls":[{"function":{"name":"weather","arguments":"{\\"city\\":\\"北京\\"}"}}]}')
        self.assertEqual(d["tool_calls"][0]["function"]["name"], "weather")
        self.assertIn("北京", d["tool_calls"][0]["function"]["arguments"])

    def test_code_fence_and_prose(self):
        raw = '好的，需要查天气：\n```json\n{"tool_calls":[{"name":"weather","arguments":{}}]}\n```'
        d = copilot_tools.parse_decision(raw)
        self.assertEqual(d["tool_calls"][0]["function"]["name"], "weather")

    def test_content_shape(self):
        d = copilot_tools.parse_decision('{"content":"不用工具，直接回答"}')
        self.assertEqual(d["content"], "不用工具，直接回答")
        self.assertIsNone(d["tool_calls"])

    def test_plain_text_falls_back(self):
        d = copilot_tools.parse_decision("北京今天晴，25 度。")
        self.assertEqual(d["content"], "北京今天晴，25 度。")
        self.assertIsNone(d["tool_calls"])

    def test_invalid_arguments_falls_back(self):
        d = copilot_tools.parse_decision(
            '{"tool_calls":[{"name":"weather","arguments":"not-json"}]}')
        self.assertIsNone(d["tool_calls"])
        self.assertTrue(d["content"])


class PromptTests(unittest.TestCase):
    def test_prompt_contains_tool_and_transcript(self):
        msgs = [b.Message(role="user", content="北京天气")]
        tools = [{"type": "function",
                  "function": {"name": "weather", "description": "查天气",
                               "parameters": {"type": "object"}}}]
        prompt = copilot_tools.build_prompt(msgs, tools)
        self.assertIn("weather", prompt)
        self.assertIn("北京天气", prompt)
        self.assertIn("只输出", prompt)


class RouteTests(unittest.TestCase):
    def test_copilot_route_returns_synthesized_tool_calls(self):
        from fastapi.testclient import TestClient

        from gantry import api as api_mod

        old_flag = b.CFG.get("copilot_tool_shim")
        old_decide = copilot_tools.decide
        calls = [{"id": "call_1", "type": "function",
                  "function": {"name": "weather", "arguments": "{}"}}]

        async def fake_decide(req):
            return {"content": None, "tool_calls": calls}

        b.CFG["copilot_tool_shim"] = True
        copilot_tools.decide = fake_decide
        try:
            c = TestClient(api_mod.app, base_url="http://127.0.0.1:8765")
            r = c.post("/v1/chat/completions", json={
                "model": "copilot-auto", "stream": False,
                "tools": [{"type": "function",
                           "function": {"name": "weather", "parameters": {"type": "object"}}}],
                "messages": [{"role": "user", "content": "北京天气"}],
            })
            self.assertEqual(r.status_code, 200, r.text[:300])
            choice = r.json()["choices"][0]
            self.assertEqual(choice["finish_reason"], "tool_calls")
            self.assertEqual(choice["message"]["tool_calls"], calls)
        finally:
            copilot_tools.decide = old_decide
            if old_flag is None:
                b.CFG.pop("copilot_tool_shim", None)
            else:
                b.CFG["copilot_tool_shim"] = old_flag
