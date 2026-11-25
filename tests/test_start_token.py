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
        self.assertIsNone(token)
        self.assertFalse(f.exists(), ".env 里有实值时应尊重配置")

    def test_plaintext_token_respected(self):
        token, f = self._call("plain-token", None)
        self.assertIsNone(token)
        self.assertFalse(f.exists())


if __name__ == "__main__":
    unittest.main()
