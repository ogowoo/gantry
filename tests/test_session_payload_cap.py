# -*- coding: utf-8 -*-
"""大 step/recent_steps 必须落库前截断，避免 agent.db 被 132MB 工具结果拖死。"""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.session_store import SessionStore


class PayloadCapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sess_cap_"))
        self.store = SessionStore(self.tmp / "agent.db")
        self.store.create("s1", "task", str(self.tmp), "copilot-auto")

    def test_recent_steps_is_truncated(self):
        big = "x" * 50000
        self.store.patch("s1", recent_steps=[{"tool": "read", "result": big}])
        got = self.store.get("s1")["recent_steps"]
        text = str(got)
        self.assertLess(len(text), 20000)
        self.assertIn("truncated", text)

    def test_step_payload_is_truncated(self):
        self.store.add_step("s1", 1, 0, {"tool": "read", "status": "ok",
                                         "result": "y" * 50000})
        con = sqlite3.connect(self.tmp / "agent.db")
        size = con.execute("select length(payload) from steps where session_id='s1'").fetchone()[0]
        con.close()
        self.assertLess(size, 20000)

    def test_detail_drops_recent_steps(self):
        from fastapi.testclient import TestClient
        from gantry import bridge as b
        from gantry import api as api_mod
        saved = b.AGENT_SESSIONS
        b.AGENT_SESSIONS = self.store
        try:
            c = TestClient(api_mod.app)
            d = c.get("/v1/agent/sessions/s1").json()["session"]
        finally:
            b.AGENT_SESSIONS = saved
        self.assertNotIn("recent_steps", d)


if __name__ == "__main__":
    unittest.main()
