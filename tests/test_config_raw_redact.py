"""配置/会话安全修复回归：raw 配置脱敏与占位符还原、删除会话 URL 白名单。

对应审查报告 M18（GET /v1/config/raw 明文导出密钥）与 M19（delete_session
接受任意 urls 并用带 SSO 登录态的浏览器导航）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 先导入 api 再导入 routes_*：routes_config ↔ api 有循环引用，
# 首个导入必须是 api（与 tests/test_api_hardening.py 等现有测试一致）。
from gantry import api as _A  # noqa: F401
from gantry import routes_config as RC
from gantry import routes_sessions as RS


class RedactConfigTests(unittest.TestCase):
    def test_sensitive_keys_masked(self):
        cfg = {
            "api_token": "sk-super-secret",
            "providers": {"anthropic": {"api_key": "ak-live"}},
            "password": "hunter2",
            "update": {"github_token": "ghp_x"},
        }
        out = RC._redact_config(cfg)
        self.assertEqual(out["api_token"], "***")
        self.assertEqual(out["providers"]["anthropic"]["api_key"], "***")
        self.assertEqual(out["password"], "***")
        self.assertEqual(out["update"]["github_token"], "***")
        # 非敏感键不动
        self.assertEqual(out["providers"], {"anthropic": {"api_key": "***"}})

    def test_secret_not_leaked_in_redacted_output(self):
        cfg = {"api_token": "TOPSECRET", "nested": {"secret_key": "ALSOSECRET"}}
        import json
        self.assertNotIn("TOPSECRET", json.dumps(RC._redact_config(cfg), ensure_ascii=False))
        self.assertNotIn("ALSOSECRET", json.dumps(RC._redact_config(cfg), ensure_ascii=False))

    def test_case_insensitive_key_match(self):
        self.assertTrue(RC._is_sensitive_key("APITOKEN"))
        self.assertTrue(RC._is_sensitive_key("AccessToken"))
        self.assertFalse(RC._is_sensitive_key("model"))
        self.assertFalse(RC._is_sensitive_key("timeout_seconds"))


class RestoreRedactedTests(unittest.TestCase):
    def test_placeholder_keeps_old_value(self):
        old = {"api_token": "real-token", "model": "gpt"}
        new = {"api_token": "***", "model": "gpt-5"}
        RC._restore_redacted(new, old)
        self.assertEqual(new["api_token"], "real-token", "占位符必须还原为旧值")
        self.assertEqual(new["model"], "gpt-5", "非占位符正常写入")

    def test_real_new_value_not_restored(self):
        old = {"api_token": "old"}
        new = {"api_token": "brand-new"}
        RC._restore_redacted(new, old)
        self.assertEqual(new["api_token"], "brand-new", "用户真改的值不能还原")

    def test_non_sensitive_placeholder_not_restored(self):
        old = {"note": "hello"}
        new = {"note": "***"}
        RC._restore_redacted(new, old)
        self.assertEqual(new["note"], "***", "非敏感键的 *** 是用户真写的，不能动")

    def test_nested_dict_restored(self):
        old = {"providers": {"anthropic": {"api_key": "ak-old"}}}
        new = {"providers": {"anthropic": {"api_key": "***"}}}
        RC._restore_redacted(new, old)
        self.assertEqual(new["providers"]["anthropic"]["api_key"], "ak-old")


class CopilotDeleteUrlsTests(unittest.TestCase):
    def test_only_copilot_hosts_allowed(self):
        urls = [
            "https://copilot.cloud.microsoft/chat/conversation/abcdefgh-1234",
            "https://copilot.microsoft.com/chat/conversation/abcdefgh-1234",
            "https://evil.example/chat/conversation/abcdefgh-1234",   # 路径像但域名不对
            "https://notcopilot.cloud.microsoft.evil.com/chat/conversation/abcdefgh-1234",
            "not a url",
        ]
        out = RS._copilot_delete_urls(urls)
        self.assertEqual(len(out), 2)
        self.assertTrue(all("copilot." in u for u in out))

    def test_empty_and_garbage(self):
        self.assertEqual(RS._copilot_delete_urls([]), [])
        self.assertEqual(RS._copilot_delete_urls(None), [])
        self.assertEqual(RS._copilot_delete_urls(["javascript:alert(1)"]), [])

    def test_dedup_preserves_order(self):
        u = "https://copilot.cloud.microsoft/chat/conversation/abcdefgh-1234"
        self.assertEqual(RS._copilot_delete_urls([u, u, u]), [u])


if __name__ == "__main__":
    unittest.main()
