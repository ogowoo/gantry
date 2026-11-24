# -*- coding: utf-8 -*-
"""本地模型管理 API：列表分类 / 整理 / 设为当前 / 启动自愈 / 下载归位 / 删除。

用合成 GGUF + 假 provider（不加载真实模型），验证"下载了却启动不了"这类
路径/类别问题在接口层就被识别并纠正。
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gantry import bridge as b
from gantry import api as _api  # noqa: F401  （先初始化 api，routes_config 里有反向导入）
from gantry import routes_config as rc
from tests.test_model_store import write_gguf


class _FakeEngine:
    def __init__(self, path=""):
        self.path = path
        self.loaded = False
        self.calls = 0

    def warm(self):
        self.loaded = True
        return {"ok": True, "loaded": True}

    def unload(self):
        self.loaded = False

    def stats(self):
        return {"path": self.path, "loaded": self.loaded, "calls": self.calls}

    def cancel(self):
        pass


class _FakeProvider:
    backend = "local"

    def __init__(self, model_path, *, engine=None):
        self.model_path = model_path
        self.engine = engine or _FakeEngine(model_path)
        self.eager = False
        self.name = "local"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_mapi_"))
        self.root = self.tmp / "data" / "models"
        self.root.mkdir(parents=True)
        self.saved = (b.BASE, dict(b.CFG), dict(b.PROVIDERS))
        b.BASE = self.tmp
        b.CFG.clear()
        b.CFG.update({"providers": {}})
        b.PROVIDERS.clear()

    def tearDown(self):
        base, cfg, provs = self.saved
        b.BASE = base
        b.CFG.clear()
        b.CFG.update(cfg)
        b.PROVIDERS.clear()
        b.PROVIDERS.update(provs)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self):
        p = self.tmp / "config.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


class ListTests(Base):
    def test_list_reports_kind_and_layout(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        write_gguf(self.root / "bge-x.gguf", arch="bert")
        r = self.c.get("/v1/models/local")
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        by_name = {m["name"]: m for m in data["models"]}
        self.assertEqual(by_name["chat-a.gguf"]["kind"], "chat")
        self.assertEqual(by_name["bge-x.gguf"]["kind"], "embedding")
        self.assertTrue(by_name["bge-x.gguf"]["needs_move"])
        self.assertEqual(data["needs_organize"], 2)

    def test_list_flags_broken_file(self):
        write_gguf(self.root / "cut.gguf", arch="llama",
                   tensors=[("w", [1024], 0)], data_bytes=64)
        data = self.c.get("/v1/models/local").json()
        item = [m for m in data["models"] if m["name"] == "cut.gguf"][0]
        self.assertFalse(item["valid"])
        self.assertTrue(any("不完整" in x for x in item["issues"]), item["issues"])
        self.assertEqual(data["broken"], 1)


class OrganizeApiTests(Base):
    def test_scan_dry_run_then_apply(self):
        write_gguf(self.root / "chat-a.gguf", arch="llama")
        plan = self.c.post("/v1/models/local/scan", json={"apply": False}).json()
        self.assertEqual(plan["moved"], 0)
        self.assertEqual(plan["actions"][0]["action"], "move")
        self.assertTrue((self.root / "chat-a.gguf").is_file())
        done = self.c.post("/v1/models/local/scan", json={"apply": True}).json()
        self.assertEqual(done["moved"], 1)
        self.assertTrue((self.root / "chat" / "chat-a.gguf").is_file())


class UseTests(Base):
    def test_use_sets_config_and_model(self):
        write_gguf(self.root / "chat" / "m.gguf", arch="llama")
        r = self.c.post("/v1/models/local/local/use", json={"path": "chat/m.gguf"})
        self.assertEqual(r.status_code, 200, r.text)
        cfg = self.config()
        mp = cfg["providers"]["local"]["model_path"]
        self.assertTrue(mp.endswith("m.gguf"))
        self.assertEqual(cfg["providers"]["local"]["backend"], "local")

    def test_use_rejects_non_chat(self):
        write_gguf(self.root / "embedding" / "e.gguf", arch="bert")
        r = self.c.post("/v1/models/local/local/use", json={"path": "embedding/e.gguf"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("向量", r.json()["detail"])

    def test_use_rejects_traversal(self):
        r = self.c.post("/v1/models/local/local/use", json={"path": "../../x.gguf"})
        self.assertEqual(r.status_code, 400)

    def test_use_missing_file_404(self):
        r = self.c.post("/v1/models/local/local/use", json={"path": "chat/none.gguf"})
        self.assertEqual(r.status_code, 404)


class StartSelfHealTests(Base):
    def setUp(self):
        super().setUp()
        # 自愈会按新路径重建 providers（真实 LocalProvider 会去加载合成文件而失败）；
        # 这里只验证"路径被改正 + 继续启动"，所以让重建保持假 provider。
        orig = rc._reload_providers
        rc._reload_providers = lambda: None
        self.addCleanup(lambda: setattr(rc, "_reload_providers", orig))
    def test_start_repairs_missing_path_by_filename(self):
        write_gguf(self.root / "chat" / "moved.gguf", arch="llama")
        b.PROVIDERS["local"] = _FakeProvider(str(self.root / "old" / "moved.gguf"))
        r = self.c.post("/v1/models/local/local/start", json={})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertTrue(data["ok"], data)
        self.assertIsNotNone(data.get("repaired"))
        cfg = self.config()
        self.assertTrue(cfg["providers"]["local"]["model_path"].endswith("moved.gguf"))

    def test_start_404_when_nowhere_to_find(self):
        b.PROVIDERS["local"] = _FakeProvider(str(self.root / "chat" / "ghost.gguf"))
        r = self.c.post("/v1/models/local/local/start", json={})
        self.assertEqual(r.status_code, 404)
        self.assertIn("ghost.gguf", r.json()["detail"])

    def test_start_rejects_embedding(self):
        write_gguf(self.root / "embedding" / "e.gguf", arch="bert")
        b.PROVIDERS["local"] = _FakeProvider(str(self.root / "embedding" / "e.gguf"))
        r = self.c.post("/v1/models/local/local/start", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("向量", r.json()["detail"])

    def test_start_rejects_truncated(self):
        p = write_gguf(self.root / "chat" / "cut.gguf", arch="llama",
                       tensors=[("w", [1024], 0)], data_bytes=64)
        b.PROVIDERS["local"] = _FakeProvider(str(p))
        r = self.c.post("/v1/models/local/local/start", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不完整", r.json()["detail"])

    def test_start_happy_path_returns_kind(self):
        p = write_gguf(self.root / "chat" / "ok.gguf", arch="llama")
        b.PROVIDERS["local"] = _FakeProvider(str(p))
        r = self.c.post("/v1/models/local/local/start", json={})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kind"], "chat")


class DownloadTests(Base):
    def _patch_download(self, writer):
        orig = rc._download_model_sync

        def fake(url, dest, max_bytes):
            dest.parent.mkdir(parents=True, exist_ok=True)
            writer(dest)
            return dest.stat().st_size

        rc._download_model_sync = fake
        self.addCleanup(lambda: setattr(rc, "_download_model_sync", orig))

    def test_download_places_file_by_kind(self):
        def writer(dest):
            write_gguf(dest, arch="bert")
        self._patch_download(writer)
        r = self.c.post("/v1/models/local/download",
                        json={"url": "https://x/y/bge-test.gguf"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["kind"], "embedding")
        self.assertEqual(data["path"], "embedding/bge-test.gguf")
        self.assertTrue((self.root / "embedding" / "bge-test.gguf").is_file())

    def test_download_kind_hint_overrides(self):
        def writer(dest):
            write_gguf(dest, arch="llama")
        self._patch_download(writer)
        r = self.c.post("/v1/models/local/download",
                        json={"url": "https://x/y/m.gguf", "kind": "mmproj"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["path"], "mmproj/m.gguf")

    def test_download_rejects_garbage(self):
        def writer(dest):
            dest.write_bytes(b"NOPE" + b"\0" * 64)
        self._patch_download(writer)
        r = self.c.post("/v1/models/local/download", json={"url": "https://x/y/bad.gguf"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("GGUF", r.json()["detail"])
        self.assertFalse((self.root / "bad.gguf").exists())

    def test_download_rejects_truncated(self):
        def writer(dest):
            write_gguf(dest, arch="llama", tensors=[("w", [1024], 0)], data_bytes=32)
        self._patch_download(writer)
        r = self.c.post("/v1/models/local/download", json={"url": "https://x/y/cut.gguf"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不完整", r.json()["detail"])
        self.assertFalse((self.root / "cut.gguf").exists())

    def test_download_conflict_suffix(self):
        (self.root / "embedding").mkdir()
        write_gguf(self.root / "embedding" / "m.gguf", arch="bert",
                   kv={"general.name": "first"})

        def writer(dest):
            write_gguf(dest, arch="bert")
        self._patch_download(writer)
        r = self.c.post("/v1/models/local/download", json={"url": "https://x/y/m.gguf"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["path"], "embedding/m-1.gguf")


class DeleteTests(Base):
    def test_delete_subdir(self):
        write_gguf(self.root / "chat" / "m.gguf", arch="llama")
        r = self.c.delete("/v1/models/local/chat/m.gguf")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse((self.root / "chat" / "m.gguf").exists())

    def test_delete_traversal_blocked(self):
        r = self.c.delete("/v1/models/local/..%2F..%2Fsecret.txt")
        self.assertIn(r.status_code, (400, 404), r.text)

    def test_delete_missing_404(self):
        r = self.c.delete("/v1/models/local/chat/none.gguf")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
