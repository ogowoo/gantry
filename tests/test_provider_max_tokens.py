# -*- coding: utf-8 -*-
"""OpenAI 协议的两个缺口（配本地 Ollama 时暴露）：

1. `max_tokens` 以前只发给 anthropic，OpenAI 分支**从没发过** → 思考型模型（Qwen3.5 等）
   会把服务端默认预算全烧在 reasoning 上，`content` 直接为空；
2. 只拿到 reasoning 时旧代码返回空字符串 → agent 侧只看到"模型不返回动作"，
   完全看不出是 token 预算问题。现在报一条**可行动**的错误。

这两条都用假的 httpx 响应覆盖，不联网。
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
    def __init__(self, resp: _FakeResp, sink: list):
        self._resp = resp
        self._sink = sink

    async def post(self, url, json=None, **kw):
        self._sink.append((url, json))
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _provider(**cfg) -> ApiProvider:
    base = {"base_url": "http://127.0.0.1:11434/v1", "api_key": "ollama",
            "models": ["m"], "use_system_proxy": False}
    base.update(cfg)
    return ApiProvider("ollama", base)


def _run_complete(p: ApiProvider, message: dict) -> tuple[str, list]:
    sink: list = []
    payload = {"choices": [{"message": message, "finish_reason": "stop"}]}
    p._client = lambda **kw: _FakeClient(_FakeResp(payload), sink)   # type: ignore[assignment]
    out = asyncio.new_event_loop().run_until_complete(
        p.complete("m", [{"role": "user", "content": "hi"}]))
    return out, sink


class MaxTokensPayloadTests(unittest.TestCase):
    def test_payload_includes_configured_max_tokens(self):
        p = _provider(max_tokens=4096)
        _, sink = _run_complete(p, {"content": "ok"})
        self.assertTrue(sink, "没有发出请求")
        body = sink[0][1]
        self.assertEqual(body.get("max_tokens"), 4096,
                         "OpenAI 协议必须发 max_tokens（思考型模型靠它给正文留预算）")

    def test_default_is_8192(self):
        p = _provider()
        _, sink = _run_complete(p, {"content": "ok"})
        self.assertEqual(sink[0][1].get("max_tokens"), 8192)

    def test_zero_disables_field(self):
        """显式 0 = 不发该字段（让服务端用自己的默认值，某些网关不认 max_tokens）。"""
        p = _provider(max_tokens=0)
        _, sink = _run_complete(p, {"content": "ok"})
        self.assertNotIn("max_tokens", sink[0][1])


class ReasoningOnlyTests(unittest.TestCase):
    def test_reasoning_without_content_is_actionable(self):
        p = _provider(max_tokens=512)
        with self.assertRaises(ProviderError) as ctx:
            _run_complete(p, {"content": "", "reasoning": "Thinking Process: ..."})
        msg = str(ctx.exception)
        self.assertIn("max_tokens", msg, "错误里要指出 max_tokens")
        self.assertIn("512", msg, "要带上当前值，方便直接调")
        self.assertIn("ollama", msg)

    def test_reasoning_content_alias_supported(self):
        """有些实现叫 reasoning_content（DeepSeek 风格），同样要识别。"""
        p = _provider()
        with self.assertRaises(ProviderError) as ctx:
            _run_complete(p, {"content": "", "reasoning_content": "思考中"})
        self.assertIn("reasoning", str(ctx.exception))

    def test_truly_empty_response_raises(self):
        p = _provider()
        with self.assertRaises(ProviderError) as ctx:
            _run_complete(p, {"content": ""})
        self.assertIn("空正文", str(ctx.exception))

    def test_normal_content_still_returned(self):
        p = _provider()
        out, _ = _run_complete(p, {"content": "你好"})
        self.assertEqual(out, "你好")


if __name__ == "__main__":
    unittest.main()
