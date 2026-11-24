# -*- coding: utf-8 -*-
"""模型提供商配置 API：列表脱敏 / 新增修改 / 删除与恢复预设 / 测试连接 / 默认模型。

全部用临时 BASE + 空 providers，绝不碰真实 config.json。
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gantry import bridge as b
from gantry import api as _api  # noqa: F401  （先初始化 api：routes_* 有反向导入）
from gantry import providers as providers_mod


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_prov_"))
        self.saved = (b.BASE, dict(b.CFG), dict(b.PROVIDERS), b.DEFAULT_MODEL)
        b.BASE = self.tmp
        b.CFG.clear()
        b.CFG.update({"providers": {}})
        b.PROVIDERS.clear()
        b.PROVIDERS.update(providers_mod.build_providers(b.CFG))

    def tearDown(self):
        base, cfg, provs, dm = self.saved
        b.BASE = base
        b.CFG.clear()
        b.CFG.update(cfg)
        b.PROVIDERS.clear()
        b.PROVIDERS.update(provs)
        b.DEFAULT_MODEL = dm
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self):
        p = self.tmp / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def write_providers(self, provs):
        """同时写 config.json 与内存 CFG（运行期两者都由 POST /v1/providers 维护）。"""
        (self.tmp / "config.json").write_text(
            json.dumps({"providers": provs}), encoding="utf-8")
        b.CFG["providers"] = json.loads(json.dumps(provs))
        b.PROVIDERS.update(providers_mod.build_providers(b.CFG))


class ListTests(Base):
    def test_lists_presets_without_leaking_keys(self):
        r = self.c.get("/v1/providers")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        names = {p["name"] for p in data["providers"]}
        self.assertIn("deepseek", names)
        self.assertIn("kimi-coding", names)
        kinds = {p["name"]: p["kind"] for p in data["providers"]}
        self.assertEqual(kinds["deepseek"], "preset")
        for p in data["providers"]:
            self.assertNotIn("api_key", p)

    def test_custom_provider_marks_keys_without_echoing_them(self):
        (self.tmp / "config.json").write_text(json.dumps({
            "providers": {"mygateway": {"base_url": "https://gw.example/v1",
                                        "api_key": "sk-secret-should-never-leak",
                                        "models": ["m1"], "description": "自建网关"}}
        }), encoding="utf-8")
        r = self.c.get("/v1/providers")
        raw = r.text
        self.assertNotIn("sk-secret-should-never-leak", raw)
        p = {x["name"]: x for x in r.json()["providers"]}["mygateway"]
        self.assertEqual(p["kind"], "custom")
        self.assertTrue(p["api_key_set"])
        self.assertTrue(p["api_key_in_config"])
        self.assertEqual(p["models"], ["m1"])
        self.assertTrue(p["configured"])

    def test_env_key_is_detected_as_active(self):
        import os
        os.environ["CBP_TEST_PROV_KEY"] = "x"
        try:
            (self.tmp / "config.json").write_text(json.dumps({
                "providers": {"envgw": {"base_url": "https://gw.example/v1",
                                        "api_key_env": "CBP_TEST_PROV_KEY"}}
            }), encoding="utf-8")
            p = {x["name"]: x for x in self.c.get("/v1/providers").json()["providers"]}["envgw"]
            self.assertEqual(p["api_key_env_active"], "CBP_TEST_PROV_KEY")
            self.assertFalse(p["api_key_in_config"])
            self.assertTrue(p["configured"])
        finally:
            os.environ.pop("CBP_TEST_PROV_KEY", None)


class CreateUpdateTests(Base):
    def _new(self, **kw):
        body = {"name": "mygw", "base_url": "https://gw.example/v1",
                "models": "m1, m2", "api_key_env": "MYGW_KEY",
                "description": "我的网关"}
        body.update(kw)
        return self.c.post("/v1/providers", json=body)

    def test_create_writes_config_and_registers_provider(self):
        r = self._new(api_key="sk-abc")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["created"])
        self.assertEqual(data["provider"]["models"], ["m1", "m2"])
        self.assertEqual(data["provider"]["api_key_env"], ["MYGW_KEY"])
        entry = self.cfg()["providers"]["mygw"]
        self.assertEqual(entry["base_url"], "https://gw.example/v1")
        self.assertEqual(entry["api_key"], "sk-abc")
        self.assertIn("mygw", b.PROVIDERS)
        # 新提供商的模型立刻出现在 /v1/models（模型选择器动态生成）
        ids = {m["id"] for m in self.c.get("/v1/models").json()["data"]}
        self.assertIn("mygw/m1", ids)

    def test_update_without_api_key_keeps_existing(self):
        self._new(api_key="sk-keep")
        r = self._new(description="改描述")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["created"])
        entry = self.cfg()["providers"]["mygw"]
        self.assertEqual(entry["api_key"], "sk-keep")
        self.assertEqual(entry["description"], "改描述")

    def test_empty_api_key_clears_it(self):
        self._new(api_key="sk-gone")
        self._new(api_key="")
        self.assertNotIn("api_key", self.cfg()["providers"]["mygw"])
        p = {x["name"]: x for x in self.c.get("/v1/providers").json()["providers"]}["mygw"]
        self.assertFalse(p["api_key_in_config"])
        self.assertFalse(p["configured"])

    def test_preset_edit_creates_override_layer(self):
        r = self.c.post("/v1/providers", json={
            "name": "deepseek", "base_url": "https://proxy.example/v1"})
        self.assertEqual(r.status_code, 200, r.text)
        p = {x["name"]: x for x in self.c.get("/v1/providers").json()["providers"]}["deepseek"]
        self.assertEqual(p["kind"], "custom")     # 有覆盖层
        self.assertEqual(p["base_url"], "https://proxy.example/v1")
        self.assertIn("base_url", p["overrides"])
        self.assertEqual(b.PROVIDERS["deepseek"].base_url, "https://proxy.example/v1")

    def test_validation_errors(self):
        bad = [
            ({"name": "Bad Name"}, "名称只能"),
            ({"name": ""}, "缺少 name"),
            ({"name": "copilot"}, "内置通道名"),
            ({"name": "ok1", "base_url": "ftp://x"}, "http(s)"),
            ({"name": "ok1", "api": "grpc"}, "openai 或 anthropic"),
            ({"name": "ok1", "json_mode": "yaml"}, "json_mode"),
            ({"name": "ok1", "max_tokens": -1}, "max_tokens 超出范围"),
            ({"name": "ok1", "timeout_seconds": 1}, "timeout_seconds 超出范围"),
            ({"name": "ok1", "api_key_env": "1BAD"}, "环境变量名不合法"),
            ({"name": "ok1", "unexpected": 1}, "不支持的字段"),
        ]
        for body, needle in bad:
            with self.subTest(body=body):
                r = self.c.post("/v1/providers", json=body)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertIn(needle, r.text)
        self.assertFalse((self.tmp / "config.json").exists())

    def test_anthropic_and_vision_roundtrip(self):
        r = self._new(name="coding", api="anthropic", vision=True, max_tokens=8192)
        self.assertEqual(r.status_code, 200, r.text)
        entry = self.cfg()["providers"]["coding"]
        self.assertEqual(entry["api"], "anthropic")
        self.assertTrue(entry["vision"])
        p = {x["name"]: x for x in self.c.get("/v1/providers").json()["providers"]}["coding"]
        self.assertEqual(p["api"], "anthropic")
        self.assertTrue(p["vision"])


class DeleteTests(Base):
    def test_delete_custom(self):
        self.c.post("/v1/providers", json={"name": "tmpgw", "models": "m"})
        r = self.c.delete("/v1/providers/tmpgw")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["deleted"])
        self.assertNotIn("tmpgw", self.cfg().get("providers") or {})
        self.assertNotIn("tmpgw", b.PROVIDERS)

    def test_delete_preset_restores_defaults(self):
        self.c.post("/v1/providers", json={"name": "deepseek", "models": "x"})
        r = self.c.delete("/v1/providers/deepseek")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["restored_preset"])
        self.assertNotIn("deepseek", self.cfg().get("providers") or {})
        self.assertEqual(b.PROVIDERS["deepseek"].models,
                         providers_mod.PRESETS["deepseek"]["models"])

    def test_delete_untouched_preset_is_404(self):
        r = self.c.delete("/v1/providers/grok")
        self.assertEqual(r.status_code, 404, r.text)


class TestConnectionTests(Base):
    def test_local_missing_file_reports_plainly(self):
        self.write_providers({"loc": {"backend": "local",
                                      "model_path": "C:/nope/x.gguf"}})
        r = self.c.post("/v1/providers/loc/test")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertFalse(data["ok"])
        self.assertIn("不存在", data["error"])

    def test_missing_key_fails_fast_without_network(self):
        self.write_providers({"nokey": {"base_url": "http://127.0.0.1:9/v1",
                                        "api_key_env": "CBP_DEFINITELY_UNSET_KEY"}})
        r = self.c.post("/v1/providers/nokey/test")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["ok"])
        self.assertIn("API Key", r.json()["error"])

    def test_unknown_provider_404(self):
        r = self.c.post("/v1/providers/nosuch/test")
        self.assertEqual(r.status_code, 404, r.text)


class DefaultModelTests(Base):
    def test_sets_default_model_immediately(self):
        self.c.post("/v1/providers", json={"name": "mygw", "models": "m1",
                                           "api_key": "k", "base_url": "https://x/v1"})
        r = self.c.post("/v1/providers/default-model", json={"model": "mygw/m1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(b.DEFAULT_MODEL, "mygw/m1")
        self.assertEqual(self.cfg()["default_model"], "mygw/m1")

    def test_rejects_unknown_provider_or_model(self):
        r = self.c.post("/v1/providers/default-model", json={"model": "ghost/m"})
        self.assertEqual(r.status_code, 400, r.text)
        r = self.c.post("/v1/providers/default-model",
                        json={"model": "deepseek/not-in-list"})
        self.assertEqual(r.status_code, 400, r.text)
        r = self.c.post("/v1/providers/default-model", json={})
        self.assertEqual(r.status_code, 400, r.text)


if __name__ == "__main__":
    unittest.main()
