# -*- coding: utf-8 -*-
"""实时流刷新：消息落库要发 SSE messages，前端完成态/回到前台要补刷。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import bridge as b
from gantry.session_store import SessionStore


class MessageEventTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gantry_msg_evt_"))
        self.store = SessionStore(self.tmp / "s.db")
        self.store.create("s1", "demo", str(self.tmp), "copilot-auto")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_message_publishes_messages_event(self):
        with mock.patch.object(b, "publish_event") as pub:
            mid = self.store.add_message("s1", "assistant", "done", 0)
        self.assertIsNotNone(mid)
        pub.assert_called_with("s1", "messages")

    def test_duplicate_assistant_does_not_publish(self):
        self.store.add_message("s1", "assistant", "same", 0)
        with mock.patch.object(b, "publish_event") as pub:
            got = self.store.add_message("s1", "assistant", "same", 0)
        self.assertIsNone(got)
        pub.assert_not_called()


class LiveRefreshStaticTests(unittest.TestCase):
    def test_backend_and_frontend_guards(self):
        backend = (ROOT / "gantry" / "session_store.py").read_text(encoding="utf-8")
        self.assertIn('publish_event(sid, "messages")', backend)
        follow = (ROOT / "webui" / "js" / "followload.js").read_text(encoding="utf-8")
        self.assertIn("ev.type==='messages'", follow)
        self.assertIn("followup_completed", follow)
        self.assertIn("window.refreshChat", follow)
        main = (ROOT / "webui" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("visibilitychange", main)
        self.assertIn("window.refreshChat", main)


if __name__ == "__main__":
    unittest.main()
