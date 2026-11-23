# -*- coding: utf-8 -*-
"""Teams API 层安全回归（M9/M10/M11）：

- hosts 探测不得关闭 TLS 校验（verify=False 会让真实 Bearer 令牌被中间人截获）；
- context 级抓包钩子只在 Teams 域页面生效，取数后清空页面缓存数组；
- skypeToken 缓存必须按 exp / 50 分钟 TTL 过期，过期即清。
"""
import base64
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import teams_api  # noqa: E402


def _jwt(exp: float) -> str:
    """造一个带指定 exp 的假 JWT（不验签，仅供解析）。"""

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return b64({"alg": "none"}) + "." + b64({"exp": exp}) + ".sig"


class TlsVerifyTests(unittest.TestCase):
    def test_no_verify_false_anywhere(self):
        src = (ROOT / "gantry" / "teams_api.py").read_text(encoding="utf-8")
        self.assertNotIn("verify=False", src)

    def test_hosts_probe_uses_default_ca_verification(self):
        src = (ROOT / "gantry" / "teams_api.py").read_text(encoding="utf-8")
        self.assertIn("httpx.AsyncClient(timeout=25, follow_redirects=True)", src)


class HookGuardTests(unittest.TestCase):
    def test_hook_guards_on_teams_domains(self):
        js = teams_api._HOOK_JS
        # 钩子必须先判断 location.host，非 Teams 域直接放行
        self.assertIn("location.hostname", js)
        self.assertIn("teams.cloud.microsoft", js)
        self.assertIn("teams.microsoft.com", js)

    def test_hook_capture_clears_page_arrays(self):
        src = (ROOT / "gantry" / "teams_api.py").read_text(encoding="utf-8")
        # 取完数据后必须清空 __gantryCaptured / __gantryResponses
        self.assertIn("window.__gantryCaptured.length = 0", src)
        self.assertIn("window.__gantryResponses.length = 0", src)

    def test_tokens_all_reads_module_stash(self):
        src = (ROOT / "gantry" / "teams_api.py").read_text(encoding="utf-8")
        self.assertIn("_LAST_HOOK", src)


class SkypeFreshnessTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(teams_api._CAPTURED)
        teams_api._CAPTURED.clear()

    def tearDown(self):
        teams_api._CAPTURED.clear()
        teams_api._CAPTURED.update(self._saved)

    def test_jwt_expired_token_is_cleared(self):
        teams_api._CAPTURED["skype"] = _jwt(time.time() - 100)
        teams_api._CAPTURED["skype_at"] = time.time()
        self.assertEqual(teams_api._fresh_skype(), "")
        self.assertNotIn("skype", teams_api._CAPTURED)
        self.assertNotIn("skype_at", teams_api._CAPTURED)

    def test_jwt_exp_fresh_token_returned(self):
        tok = _jwt(time.time() + 3600)
        teams_api._CAPTURED["skype"] = tok
        teams_api._CAPTURED["skype_at"] = time.time()
        self.assertEqual(teams_api._fresh_skype(), tok)

    def test_jwt_exp_5min_margin(self):
        # exp 只剩 4 分钟时应视为过期（提前 5 分钟兜底）
        teams_api._CAPTURED["skype"] = _jwt(time.time() + 240)
        teams_api._CAPTURED["skype_at"] = time.time()
        self.assertEqual(teams_api._fresh_skype(), "")

    def test_ttl_fallback_50min(self):
        # 解析不出 exp 的令牌退回 skype_at + 50 分钟 TTL
        teams_api._CAPTURED["skype"] = "aaa.bbb.ccc"
        teams_api._CAPTURED["skype_at"] = time.time() - 49 * 60
        self.assertEqual(teams_api._fresh_skype(), "aaa.bbb.ccc")
        teams_api._CAPTURED["skype_at"] = time.time() - 51 * 60
        self.assertEqual(teams_api._fresh_skype(), "")

    def test_no_timestamp_not_trusted(self):
        # 无 exp 又无 skype_at 的缓存不可信，强制清掉重取
        teams_api._CAPTURED["skype"] = "aaa.bbb.ccc"
        self.assertEqual(teams_api._fresh_skype(), "")

    def test_empty_cache(self):
        self.assertEqual(teams_api._fresh_skype(), "")


if __name__ == "__main__":
    unittest.main()
