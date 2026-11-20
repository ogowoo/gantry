# -*- coding: utf-8 -*-
"""右侧文件预览面板的结构回归。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HTML = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
PREVIEW = (ROOT / "webui" / "js" / "preview.js").read_text(encoding="utf-8")
MSG = (ROOT / "webui" / "js" / "msgrender.js").read_text(encoding="utf-8")
TOOL = (ROOT / "webui" / "js" / "toollog.js").read_text(encoding="utf-8")
CSS = (ROOT / "webui" / "css" / "preview.css").read_text(encoding="utf-8")


class PreviewPanelTests(unittest.TestCase):
    def test_index_has_panel_and_css(self):
        self.assertIn('id="previewPanel"', HTML)
        self.assertIn('css/preview.css', HTML)
        for act in ("closePreview", "previewReload", "previewDownload"):
            self.assertIn('data-act="' + act + '"', HTML)

    def test_preview_uses_sandboxed_frame_and_session_endpoint(self):
        self.assertIn("/v1/agent/sessions/", PREVIEW)
        self.assertIn('sandbox=""', PREVIEW)
        self.assertIn("HTML · sandbox", PREVIEW)
        self.assertIn("pv-text", PREVIEW)
        self.assertIn("pv-image", PREVIEW)

    def test_file_cards_and_changes_have_preview_buttons(self):
        self.assertIn('data-act="openPreview"', MSG)
        self.assertIn("data-preview", TOOL)
        self.assertIn("chg-pv", TOOL)
        self.assertIn("window.openPreview", TOOL)

    def test_css_has_panel_and_mobile_fallback(self):
        self.assertIn(".previewpanel", CSS)
        self.assertIn("max-width:860px", CSS)
        self.assertIn(".chg-pv", CSS)


if __name__ == "__main__":
    unittest.main()
