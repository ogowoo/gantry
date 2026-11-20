# -*- coding: utf-8 -*-
"""法律法规下载脚本的离线结构测试。"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("law_download_test", ROOT / "scripts" / "law_download.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["law_download_test"] = mod
spec.loader.exec_module(mod)


class LawDownloadTests(unittest.TestCase):
    def test_safe_names(self):
        self.assertEqual(mod._safe_name('民法典/2020:正式'), "民法典_2020_正式")
        self.assertTrue(mod._safe_name("").startswith("law"))

    def test_official_base(self):
        self.assertEqual(mod.BASE, "https://flk.npc.gov.cn")
        self.assertIn("/law-search/download/pc", (ROOT / "scripts" / "law_download.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
