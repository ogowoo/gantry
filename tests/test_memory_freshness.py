# -*- coding: utf-8 -*-
"""Batch 5 memory metadata / freshness / verification。"""
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.memory import MemoryStore


class MemoryFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="memory5_"))
        self.db = self.tmp / "memory.db"
        self.store = MemoryStore(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verified_source_and_ttl_roundtrip(self):
        r = self.store.remember("global", "端口是 8765", verified=True,
                                source="tests", ttl_days=10, confidence=0.9)
        self.assertTrue(r["verified"])
        hits = self.store.search("端口", ["global"])
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["verified"])
        self.assertEqual(hits[0]["source"], "tests")
        self.assertEqual(hits[0]["confidence"], 0.9)

    def test_expired_entries_hidden(self):
        r = self.store.remember("global", "临时事实", ttl_days=1)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE memories SET expires_at=? WHERE id=?", (time.time() - 1, r["id"]))
        self.assertEqual(self.store.search("临时", ["global"]), [])
        self.assertEqual(self.store.list(["global"]), [])

    def test_unverified_does_not_override_verified(self):
        self.store.remember("global", "同一事实", verified=True, source="a")
        self.store.remember("global", "同一事实", verified=False, source="b")
        hit = self.store.search("同一事实", ["global"])[0]
        self.assertTrue(hit["verified"])
        self.assertIn(hit["source"], {"a", "b"})

    def test_legacy_table_is_migrated(self):
        legacy = self.tmp / "legacy.db"
        with sqlite3.connect(legacy) as con:
            con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "scope TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact', text TEXT NOT NULL, "
                        "tags TEXT NOT NULL DEFAULT '[]', source_session TEXT, pinned INTEGER NOT NULL DEFAULT 0, "
                        "use_count INTEGER NOT NULL DEFAULT 0, last_used REAL, created REAL NOT NULL, "
                        "updated REAL NOT NULL, norm TEXT NOT NULL)")
        store = MemoryStore(legacy)
        store.remember("global", "迁移后的记忆", verified=True)
        hit = store.search("迁移", ["global"])[0]
        self.assertTrue(hit["verified"])


if __name__ == "__main__":
    unittest.main()
