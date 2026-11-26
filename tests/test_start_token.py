# -*- coding: utf-8 -*-
"""AUD-P0-03：api_token 占位符语义——未解析/空值必须走生成，实值才尊重配置。"""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("gantry_start", ROOT / "start.py")
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)


class StartTokenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_tok_"))
        self.orig = (start.CFG, start.SERVER, start.BASE,
                     os.environ.get("GANTRY_TOKEN"), os.environ.get("GANTRY_API_TOKEN"))

    def tearDown(self):
        start.CFG, start.SERVER, start.BASE = self.orig[:3]
        for key, val in (("GANTRY_TOKEN", self.orig[3]), ("GANTRY_API_TOKEN", self.orig[4])):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, cfg_token, env_token):
        start.BASE = self.tmp
        start.CFG = {"api_token": cfg_token}
        start.SERVER = {"host": "127.0.0.1", "port": 8765}
        os.environ.pop("GANTRY_TOKEN", None)
        os.environ.pop("GANTRY_API_TOKEN", None)
        if env_token is not None:
            os.environ["GANTRY_TOKEN"] = env_token
        start.ensure_api_token()
        return os.environ.get("GANTRY_API_TOKEN"), (self.tmp / "data" / "api_token")

    def test_placeholder_without_env_generates_token(self):
        token, f = self._call("${GANTRY_TOKEN}", None)
        self.assertTrue(token and f.exists(),
                        "未解析占位符是公开常量，必须走生成（AUD-P0-03）")

    def test_placeholder_with_empty_env_generates_token(self):
        token, f = self._call("${GANTRY_TOKEN}", "")
        self.assertTrue(token and f.exists(),
                        "占位符解析为空=鉴权停用，必须走生成（AUD-P0-03）")

    def test_placeholder_with_real_env_respected(self):
        token, f = self._call("${GANTRY_TOKEN}", "REAL-TOKEN")
        self.assertEqual(token, "REAL-TOKEN",
                         "配置占位符解析出的实值必须传给 GANTRY_API_TOKEN（托盘/CLI 只认它和文件）")
        self.assertTrue(f.exists(), "data/api_token 缺失会让托盘/CLI 拿不到凭据")
        self.assertEqual(f.read_text(encoding="utf-8").strip(), "REAL-TOKEN")

    def test_plaintext_token_respected(self):
        token, f = self._call("plain-token", None)
        self.assertEqual(token, "plain-token",
                         "配置的字面量 token 必须传给 GANTRY_API_TOKEN")
        self.assertTrue(f.exists())
        self.assertEqual(f.read_text(encoding="utf-8").strip(), "plain-token")

    def test_config_token_syncs_stale_file(self):
        """文件里是旧值、config 给了新实值 → 以 config 为准并把文件同步过去，
        否则托盘/CLI 拿旧 token 打新服务会一直 401。"""
        start.BASE = self.tmp
        start.CFG = {"api_token": "NEW-TOKEN"}
        start.SERVER = {"host": "127.0.0.1", "port": 8765}
        os.environ.pop("GANTRY_TOKEN", None)
        os.environ.pop("GANTRY_API_TOKEN", None)
        f = self.tmp / "data" / "api_token"
        f.parent.mkdir(parents=True)
        f.write_text("OLD-TOKEN\n", encoding="utf-8")
        start.ensure_api_token()
        self.assertEqual(os.environ.get("GANTRY_API_TOKEN"), "NEW-TOKEN")
        self.assertEqual(f.read_text(encoding="utf-8").strip(), "NEW-TOKEN")

    def test_existing_file_token_exported(self):
        """config 没配 token：文件里的就是生效值，环境变量必须同步出去。"""
        start.BASE = self.tmp
        start.CFG = {"api_token": ""}
        start.SERVER = {"host": "127.0.0.1", "port": 8765}
        os.environ.pop("GANTRY_TOKEN", None)
        os.environ.pop("GANTRY_API_TOKEN", None)
        f = self.tmp / "data" / "api_token"
        f.parent.mkdir(parents=True)
        f.write_text("FILE-TOKEN\n", encoding="utf-8")
        start.ensure_api_token()
        self.assertEqual(os.environ.get("GANTRY_API_TOKEN"), "FILE-TOKEN")
        self.assertEqual(f.read_text(encoding="utf-8").strip(), "FILE-TOKEN")


if __name__ == "__main__":
    unittest.main()
