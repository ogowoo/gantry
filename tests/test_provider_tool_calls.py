# -*- coding: utf-8 -*-
"""提供商 tool_calls 透传：DSH 等 Agent 客户端需要用工具。

OpenAI 兼容提供商返回的 `message.tool_calls` 不能被吞掉——否则客户端永远
看不到工具调用，Agent 循环走不下去。非流式直接透传；流式按原分片转发。
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.providers import ApiProvider, ProviderError  # noqa: E402


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def post(self, url, json=None, **kw):
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _provider() -> ApiProvider:
    return ApiProvider("ollama", {"base_url": "http://127.0.0.1:11434/v1",
                                  "api_key": "ollama", "models": ["m"],
                                  "use_system_proxy": False})


def _with_resp(p: ApiProvider, message: dict, finish: str = "tool_calls"):
    payload = {"choices": [{"message": message, "finish_reason": finish}]}
    p._client = lambda **kw: _FakeClient(_FakeResp(payload))   # type: ignore[assignment]


class ToolCallsTests(unittest.TestCase):
    def test_complete_message_keeps_tool_calls(self):
        calls = [{"id": "t1", "type": "function",
                  "function": {"name": "weather", "arguments": "{\"city\":\"北京\"}"}}]
        p = _provider()
        _with_resp(p, {"content": None, "tool_calls": calls})
        msg = asyncio.new_event_loop().run_until_complete(
            p.complete_message("m", [{"role": "user", "content": "天气"}]))
        self.assertEqual(msg["tool_calls"], calls)
        self.assertEqual(msg["content"], "")
        self.assertEqual(msg["finish_reason"], "tool_calls")

    def test_complete_raises_when_only_tool_calls(self):
        p = _provider()
        _with_resp(p, {"content": None,
                       "tool_calls": [{"id": "t1", "type": "function",
                                       "function": {"name": "x", "arguments": "{}"}}]})
        with self.assertRaises(ProviderError):
            asyncio.new_event_loop().run_until_complete(
                p.complete("m", [{"role": "user", "content": "hi"}]))

    def test_text_still_works(self):
        p = _provider()
        _with_resp(p, {"content": "你好"}, finish="stop")
        msg = asyncio.new_event_loop().run_until_complete(
            p.complete_message("m", [{"role": "user", "content": "hi"}]))
        self.assertEqual(msg["content"], "你好")
        self.assertIsNone(msg["tool_calls"])
        self.assertEqual(msg["finish_reason"], "stop")

    def test_tools_forwarded_to_provider(self):
        tools = [{"type": "function", "function": {"name": "weather",
                                                   "parameters": {"type": "object"}}}]

        class _SinkClient(_FakeClient):
            def __init__(self, resp, sink):
                super().__init__(resp)
                self.sink = sink

            async def post(self, url, json=None, **kw):
                self.sink.append(json)
                return await super().post(url, json=json, **kw)

        sink: list = []
        payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        p = _provider()
        p._client = lambda **kw: _SinkClient(_FakeResp(payload), sink)  # type: ignore[assignment]
        asyncio.new_event_loop().run_until_complete(
            p.complete_message("m", [{"role": "user", "content": "hi"}],
                               tools=tools, tool_choice="auto"))
        self.assertEqual(sink[0]["tools"], tools)
        self.assertEqual(sink[0]["tool_choice"], "auto")


class ChatCompletionsRouteTests(unittest.TestCase):
    """端到端：/v1/chat/completions 要把提供商 tool_calls 原样放进 message。"""

    def test_route_returns_tool_calls(self):
        from fastapi.testclient import TestClient

        from gantry import api as api_mod
        from gantry import bridge as b

        calls = [{"id": "t1", "type": "function",
                  "function": {"name": "weather", "arguments": "{\"city\":\"北京\"}"}}]

        class _FakeProvider:
            async def complete_message(self, model, messages, response_schema=None, **kw):
                return {"content": "", "tool_calls": calls,
                        "finish_reason": "tool_calls"}

        b.PROVIDERS["fakeprov"] = _FakeProvider()
        try:
            c = TestClient(api_mod.app, base_url="http://127.0.0.1:8765")
            r = c.post("/v1/chat/completions", json={
                "model": "fakeprov/m1", "stream": False,
                "messages": [{"role": "user", "content": "北京天气"},
                             {"role": "assistant", "content": None,
                              "tool_calls": calls},
                             {"role": "tool", "content": "晴", "tool_call_id": "t1"}],
            })
            self.assertEqual(r.status_code, 200, r.text[:400])
            choice = r.json()["choices"][0]
            self.assertEqual(choice["finish_reason"], "tool_calls")
            self.assertEqual(choice["message"]["tool_calls"], calls)
            self.assertIsNone(choice["message"]["content"])
        finally:
            b.PROVIDERS.pop("fakeprov", None)


class StreamProviderToolCallsTests(unittest.TestCase):
    def test_stream_forwards_tool_call_deltas(self):
        import json

        from gantry.routes_core import stream_provider

        calls = [{"index": 0, "id": "t1", "type": "function",
                  "function": {"name": "weather", "arguments": "{}"}}]

        class _FakeProvider:
            name = "fakeprov"

            async def stream_chunks(self, model, messages, **kw):
                yield {"delta": {"role": "assistant"}, "finish_reason": None}
                yield {"delta": {"tool_calls": calls}, "finish_reason": None}
                yield {"delta": {}, "finish_reason": "tool_calls"}

        async def collect():
            return [chunk async for chunk in stream_provider(_FakeProvider(), "m", [])]

        chunks = asyncio.new_event_loop().run_until_complete(collect())
        payloads = []
        for chunk in chunks:
            text = chunk.strip()
            if text.startswith("data: ") and text[6:] != "[DONE]":
                payloads.append(json.loads(text[6:]))
        deltas = [p["choices"][0]["delta"] for p in payloads]
        self.assertTrue(any(d.get("tool_calls") == calls for d in deltas))
        self.assertEqual(payloads[-1]["choices"][0]["finish_reason"], "tool_calls")
