# -*- coding: utf-8 -*-
"""Copilot 网页模型的 tool_calls 合成层（提示词 + JSON 解析）。"""
import json
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

    def test_single_quote_escape_repaired(self):
        """实测 Copilot 会输出 \\'（非法 JSON 转义），不能让整条工具调用丢失。"""
        raw = ('{"tool_calls":[{"name":"pwsh","arguments":{"command":'
               '"$git=\'C:\\\\Program Files\\\\Git\\\\cmd\\\\git.exe\'; & $git status"}}]}')
        d = copilot_tools.parse_decision(raw)
        self.assertIsNotNone(d["tool_calls"])
        self.assertEqual(d["tool_calls"][0]["function"]["name"], "pwsh")
        args = json.loads(d["tool_calls"][0]["function"]["arguments"])
        self.assertIn("Program Files", args["command"])

    def test_raw_newline_in_string_repaired(self):
        raw = '{"tool_calls":[{"name":"x","arguments":{"text":"line1\nline2"}}]}'
        d = copilot_tools.parse_decision(raw)
        self.assertIsNotNone(d["tool_calls"])


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


class DecideTests(unittest.TestCase):
    def test_decide_keeps_a_session_for_deletion(self):
        """每轮保留 toolcall-* 会话，侧栏删除才能连带回收远端 Copilot 对话。"""
        import asyncio
        from types import SimpleNamespace

        from gantry import bridge as b

        calls = []

        async def fake_plain(req):
            calls.append(req)
            return {"choices": [{"message": {
                "content": '{"tool_calls":[{"name":"pwsh","arguments":{"command":"git status"}}]}'
            }}]}

        saved = b.plain_completions
        b.plain_completions = fake_plain                       # type: ignore[assignment]
        try:
            req = SimpleNamespace(
                model="copilot-auto",
                messages=[b.Message(role="user", content="看下 git 状态")],
                tools=[{"type": "function",
                        "function": {"name": "pwsh", "parameters": {"type": "object"}}}],
                tool_choice=None,
            )
            d = asyncio.new_event_loop().run_until_complete(copilot_tools.decide(req))
            self.assertEqual(d["tool_calls"][0]["function"]["name"], "pwsh")
            self.assertTrue(calls)
            self.assertTrue(calls[0].session_id.startswith("toolcall-"))
            self.assertFalse(calls[0].agent_mode)
        finally:
            b.plain_completions = saved


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
