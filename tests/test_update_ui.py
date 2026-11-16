# -*- coding: utf-8 -*-
"""侧栏更新提示与设置更新面板的静态回归。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class UpdateUiTests(unittest.TestCase):
    def test_version_chip_opens_update_settings(self):
        html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'id="appVersion"[^>]*data-act="openUpdateSettings"')
        self.assertIn('role="button"', html)
        self.assertIn('tabindex="0"', html)

    def test_version_js_auto_checks_and_badges(self):
        src = (ROOT / "webui" / "js" / "version.js").read_text(encoding="utf-8")
        self.assertIn("/v1/update/check", src)
        self.assertIn("UPDATE_INTERVAL_MS", src)
        self.assertIn("has-update", src)
        self.assertIn("verupd", src)
        self.assertIn("gantryUpdateInfo", src)

    def test_settings_has_update_panel_and_actions(self):
        src = (ROOT / "webui" / "js" / "settings.js").read_text(encoding="utf-8")
        self.assertIn('id="updatePanel"', src)
        self.assertIn("checkUpdate:()=>checkUpdate()", src)
        self.assertIn("rollbackUpdate:()=>rollbackUpdate()", src)
        self.assertIn("openUpdateSettings:()=>openUpdateSettings()", src)
        self.assertIn("async function openUpdateSettings", src)

    def test_i18n_has_update_badge_strings(self):
        i18n = (ROOT / "webui" / "i18n.js").read_text(encoding="utf-8")
        for key in ("检查更新", "发现新版本", "有新版本可用，点击打开更新管理"):
            self.assertIn(f'"{key}"', i18n)


if __name__ == "__main__":
    unittest.main()
