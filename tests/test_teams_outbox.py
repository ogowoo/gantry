# -*- coding: utf-8 -*-
"""发送幂等箱：reserved/sent/failed 状态机与重启后的防重复行为。"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.teams_outbox import SendOutbox  # noqa: E402


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="teams_outbox_")
        self.path = str(Path(self.tmp) / "sends.db")
        self.box = SendOutbox(self.path, lease_seconds=300)

    def tearDown(self):
        self.box.close()

    def test_reserve_then_sent_blocks_duplicate(self):
        r = self.box.reserve("c1", "m1", text="q", reply="a")
        self.assertEqual(r["action"], "reserved")
        self.assertTrue(r["send"])
        self.box.mark_sent("c1", "m1")
        again = self.box.reserve("c1", "m1", text="q", reply="a")
        self.assertEqual(again["action"], "duplicate")
        self.assertFalse(again["send"])

    def test_reserved_is_in_flight(self):
        self.box.reserve("c1", "m1")
        again = self.box.reserve("c1", "m1")
        self.assertEqual(again["action"], "in_flight")
        self.assertFalse(again["send"])

    def test_stale_reserved_never_auto_resends(self):
        old = time.time() - 1000
        self.box.reserve("c1", "m1")
        self.box._db.execute(
            "UPDATE sends SET updated_at=? WHERE conversation_id='c1' AND message_id='m1'",
            (old,))
        self.box._db.commit()
        res = self.box.reserve("c1", "m1")
        self.assertEqual(res["action"], "stale_reserved")
        self.assertFalse(res["send"])
        self.assertEqual(res["record"]["state"], "failed")

    def test_failed_never_auto_resends(self):
        self.box.reserve("c1", "m1")
        self.box.mark_failed("c1", "m1", "timeout", unknown=True)
        res = self.box.reserve("c1", "m1")
        self.assertEqual(res["action"], "failed")
        self.assertFalse(res["send"])
        self.assertIn("result unknown", res["record"]["error"])

    def test_survives_reopen(self):
        self.box.reserve("c1", "m1")
        self.box.mark_sent("c1", "m1")
        self.box.close()
        again = SendOutbox(self.path)
        try:
            res = again.reserve("c1", "m1")
            self.assertEqual(res["action"], "duplicate")
            self.assertEqual(again.stats().get("sent"), 1)
        finally:
            again.close()

    def test_missing_key_invalid(self):
        res = self.box.reserve("", "m1")
        self.assertEqual(res["action"], "invalid")
        self.assertFalse(res["send"])


if __name__ == "__main__":
    unittest.main()
