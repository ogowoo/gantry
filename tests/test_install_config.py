# -*- coding: utf-8 -*-
"""install.py 自动生成 config.json；login.py 缺配置时给可行动提示。"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("gantry_install", ROOT / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class EnsureConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gantry_install_cfg_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_from_example_when_missing(self):
        (self.tmp / "config.example.json").write_text(
            '{"server": {"port": 8765}}\n', encoding="utf-8")
        self.assertTrue(install.ensure_config(self.tmp))
        cfg = json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["server"]["port"], 8765)

    def test_existing_config_is_not_overwritten(self):
        (self.tmp / "config.example.json").write_text("{}", encoding="utf-8")
        (self.tmp / "config.json").write_text('{"keep": true}', encoding="utf-8")
        self.assertFalse(install.ensure_config(self.tmp))
        self.assertEqual((self.tmp / "config.json").read_text(encoding="utf-8"),
                         '{"keep": true}')

    def test_missing_example_is_actionable(self):
        with self.assertRaises(SystemExit):
            install.ensure_config(self.tmp)


class DocsTests(unittest.TestCase):
    def test_quickstart_says_install_creates_config(self):
        doc = (ROOT / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")
        self.assertIn("install.py", doc)
        self.assertIn("config.example.json", doc)
        self.assertIn("自动", doc)
        self.assertNotIn("copy config.example.json config.json", doc)

    def test_login_and_start_have_friendly_missing_config(self):
        for rel in ("scripts/login.py", "start.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("config.json", src)
            self.assertIn("python install.py", src)


if __name__ == "__main__":
    unittest.main()
