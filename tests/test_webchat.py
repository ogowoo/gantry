# -*- coding: utf-8 -*-
"""WebChat 提供商：提示词压平、提供商装配、API 端点（浏览器全部 mock）。"""
import asyncio
import unittest
from unittest import mock

from gantry import webchat as w
from gantry.providers import WebchatProvider, build_providers


def run(coro):
    return asyncio.run(coro)


class FlattenTests(unittest.TestCase):
    def test_roles_and_schema(self):
        out = w.flatten_messages(
            [{"role": "system", "content": "你是助手"},
             {"role": "user", "content": "你好"},
             {"role": "assistant", "content": "前文回答"}],
            {"type": "object"})
        self.assertIn("你是助手", out)
        self.assertIn("你好", out)
        self.assertIn("[助手此前的回答]", out)
        self.assertIn("JSON Schema", out)

    def test_list_content(self):
        out = w.flatten_messages(
            [{"role": "user", "content": [{"type": "text", "text": "分段"}]}])
        self.assertIn("分段", out)

    def test_empty_skipped(self):
        out = w.flatten_messages([{"role": "user", "content": "  "}])
        self.assertEqual(out, "")


class SiteRegistryTests(unittest.TestCase):
    def test_chatgpt_registered(self):
        spec = w.site_spec("chatgpt-web")
        self.assertEqual(spec["url"], "https://chatgpt.com/")
        self.assertIn("prompt-textarea", spec["input"])

    def test_unknown_site_raises(self):
        with self.assertRaises(KeyError):
            w.site_spec("no-such-site")


class ProviderClassTests(unittest.TestCase):
    def test_build_providers_wires_webchat(self):
        provs = build_providers({})
        p = provs.get("chatgpt-web")
        self.assertIsInstance(p, WebchatProvider)
        assert p is not None
        self.assertEqual(p.backend, "webchat")
        self.assertTrue(p.configured)
        self.assertIn("gpt-5", p.models)

    def test_complete_message_delegates(self):
        p = WebchatProvider("chatgpt-web",
                            {"backend": "webchat", "site": "chatgpt-web"})
        with mock.patch.object(w, "chat", return_value="回答") as chat:
            msg = run(p.complete_message(
                "gpt-5", [{"role": "user", "content": "问"}]))
        chat.assert_awaited_once()
        self.assertEqual(msg["content"], "回答")
        self.assertEqual(msg["finish_reason"], "stop")

    def test_stream_chunks_single_delta(self):
        p = WebchatProvider("chatgpt-web",
                            {"backend": "webchat", "site": "chatgpt-web"})
        with mock.patch.object(w, "chat", return_value="全文"):
            chunks = run(_collect(p.stream_chunks(
                "gpt-5", [{"role": "user", "content": "问"}])))
        self.assertEqual(chunks[0]["delta"]["content"], "全文")
        self.assertEqual(chunks[-1]["finish_reason"], "stop")

    def test_require_ready_unknown_site(self):
        p = WebchatProvider("x", {"backend": "webchat", "site": "nope"})
        from gantry.providers import ProviderError
        with self.assertRaises(ProviderError):
            p.require_ready()


async def _collect(agen):
    out = []
    async for c in agen:
        out.append(c)
    return out


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def test_list_shows_webchat_fields(self):
        r = self.c.get("/v1/providers")
        self.assertEqual(r.status_code, 200)
        p = next((x for x in r.json()["providers"]
                  if x["name"] == "chatgpt-web"), None)
        self.assertIsNotNone(p)
        self.assertEqual(p["backend"], "webchat")
        self.assertEqual(p["web_url"], "https://chatgpt.com/")

    def test_test_endpoint_webchat_branch(self):
        with mock.patch.object(w, "check_login",
                               return_value=(True, "")) as chk:
            r = self.c.post("/v1/providers/chatgpt-web/test")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["kind"], "webchat")
        chk.assert_awaited_once()

    def test_webchat_open_rejects_non_webchat(self):
        r = self.c.post("/v1/providers/openai/webchat-open")
        self.assertEqual(r.status_code, 400)

    def test_webchat_open_unknown_provider(self):
        r = self.c.post("/v1/providers/notaprovider/webchat-open")
        self.assertEqual(r.status_code, 404)

    def test_webchat_open_ok(self):
        fake_page = mock.Mock()
        with mock.patch.object(w, "ensure_site_page",
                               return_value=fake_page) as ep:
            r = self.c.post("/v1/providers/chatgpt-web/webchat-open")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        ep.assert_awaited_once_with("chatgpt-web", wait_load=False)


if __name__ == "__main__":
    unittest.main()
