# -*- coding: utf-8 -*-
"""OAuth 一键授权：token 存储 / 设备码状态机 / 刷新 / 回跳 URL / API。

网络全部 mock，绝不真打厂商端点；存储用临时目录。
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gantry import oauth as o


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbp_oauth_"))

    def test_roundtrip(self):
        o._save_tokens(self.root, "kimi-coding",
                       {"access_token": "a1", "refresh_token": "r1",
                        "expires_in": 900})
        rec = o.load_store(self.root)["kimi-coding"]
        self.assertEqual(rec["access_token"], "a1")
        self.assertEqual(rec["refresh_token"], "r1")
        self.assertGreater(rec["expires_at"], time.time() + 800)

    def test_get_token_valid(self):
        o._save_tokens(self.root, "kimi-coding",
                       {"access_token": "a1", "expires_in": 900})
        self.assertEqual(o.get_access_token(self.root, "kimi-coding"), "a1")

    def test_get_token_refreshes_when_expiring(self):
        o._save_tokens(self.root, "kimi-coding",
                       {"access_token": "old", "refresh_token": "r1",
                        "expires_in": 30})          # 临期（< 120s 余量）
        with mock.patch.object(o, "_post_form") as post:
            post.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"access_token": "new", "refresh_token": "r2",
                              "expires_in": 900})
            self.assertEqual(o.get_access_token(self.root, "kimi-coding"), "new")
        self.assertEqual(o.load_store(self.root)["kimi-coding"]["refresh_token"], "r2")

    def test_refresh_failure_returns_empty(self):
        o._save_tokens(self.root, "kimi-coding",
                       {"access_token": "old", "refresh_token": "bad",
                        "expires_in": 10})
        with mock.patch.object(o, "_post_form") as post:
            post.return_value = mock.Mock(status_code=401, json=lambda: {})
            self.assertEqual(o.get_access_token(self.root, "kimi-coding"), "")

    def test_disconnect(self):
        o._save_tokens(self.root, "qwen", {"access_token": "a"})
        self.assertTrue(o.disconnect(self.root, "qwen"))
        self.assertFalse(o.authorized(self.root, "qwen"))


class DeviceFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="cbp_oauth_dev_"))

    def _flow(self, **kw):
        f = {"id": "t1", "provider": "kimi-coding", "status": "pending",
             "error": "", "user_code": "ABCD-1234",
             "verification_uri": "", "verification_uri_complete": "",
             "interval": 1, "expires_in": 600, "device_code": "dc",
             "code_verifier": "", "started_at": time.time(),
             "cancel": __import__("threading").Event()}
        f.update(kw)
        return f

    def test_poll_success_saves_tokens(self):
        flow = self._flow()
        with mock.patch.object(o, "_post_form") as post:
            post.return_value = mock.Mock(
                status_code=200,
                json=lambda: {"access_token": "tok", "expires_in": 900})
            o._poll_device(self.root, "kimi-coding", flow)
        self.assertEqual(flow["status"], "ok")
        self.assertEqual(o.get_access_token(self.root, "kimi-coding"), "tok")

    def test_poll_pending_then_success(self):
        flow = self._flow()
        pending = mock.Mock(status_code=400, content=b"{}",
                            json=lambda: {"error": "authorization_pending"})
        ok = mock.Mock(status_code=200,
                       json=lambda: {"access_token": "tok2", "expires_in": 900})
        with mock.patch.object(o, "_post_form", side_effect=[pending, ok]):
            o._poll_device(self.root, "kimi-coding", flow)
        self.assertEqual(flow["status"], "ok")
        self.assertEqual(o.get_access_token(self.root, "kimi-coding"), "tok2")

    def test_poll_expired_fails(self):
        flow = self._flow()
        with mock.patch.object(o, "_post_form") as post:
            post.return_value = mock.Mock(status_code=400, content=b"{}",
                                          json=lambda: {"error": "expired_token"})
            o._poll_device(self.root, "kimi-coding", flow)
        self.assertEqual(flow["status"], "failed")
        self.assertFalse(o.authorized(self.root, "kimi-coding"))

    def test_start_device_flow_hides_device_code(self):
        with mock.patch.object(o, "_post_form") as post:
            post.return_value = mock.Mock(
                status_code=200,
                headers={"content-type": "application/json"},
                json=lambda: {"user_code": "WXYZ", "device_code": "secret-dc",
                              "verification_uri": "https://x",
                              "verification_uri_complete": "https://x?c=WXYZ",
                              "expires_in": 900, "interval": 5})
            out = o.start_device_flow(self.root, "kimi-coding")
        self.assertNotIn("device_code", out)     # bearer 等价物不出后端
        self.assertNotIn("code_verifier", out)
        self.assertEqual(out["user_code"], "WXYZ")
        o.cancel_flow(out["id"])


class CallbackFlowTests(unittest.TestCase):
    def test_authorize_url_params(self):
        spec = o.OAUTH_PROVIDERS["openai-codex"]
        url = o._build_authorize_url(spec, "http://localhost:1455/auth/callback",
                                     "ch", "st")
        self.assertIn("client_id=app_EMoamEEZ73f0CkXaXp7hrann", url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A1455", url)
        self.assertIn("code_challenge=ch", url)
        self.assertIn("state=st", url)
        self.assertIn("codex_cli_simplified_flow=true", url)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def test_providers_endpoint_lists_supported(self):
        r = self.c.get("/v1/oauth/providers")
        self.assertEqual(r.status_code, 200)
        provs = r.json()["providers"]
        self.assertIn("kimi-coding", provs)
        self.assertEqual(provs["kimi-coding"]["flow"], "device")
        self.assertEqual(provs["openai-codex"]["flow"], "callback")
        self.assertFalse(provs["kimi-coding"]["authorized"])

    def test_unknown_provider_404(self):
        r = self.c.post("/v1/oauth/notaprovider/start")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
