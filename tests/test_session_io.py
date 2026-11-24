# -*- coding: utf-8 -*-
"""会话导入导出（SessionStore.export_session / import_session）：往返保真与边界。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.session_store import SessionStore


class SessionStoreIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_sessio_"))
        self.src = SessionStore(self.tmp / "src.db")
        self.dst = SessionStore(self.tmp / "dst.db")

    def _seed(self, store, sid="s1", state="completed", parent=None):
        store.create(sid=sid, task="做一件事\n第二行", workspace="D:/ws", model="copilot",
                     title="种子会话", mode="agent")
        store.patch(sid, state=state, tag="日报", parent_id=parent)
        store.add_message(sid, "user", "你好", 0)
        store.add_message(sid, "assistant", "好的", 0)
        store.add_step(sid, 1, 0, {"tool": "read_file", "status": "ok", "reason": "看代码"})
        store.add_file_event(sid, {"tool": "edit_file", "path": "a.py", "result": "ok",
                                   "after_hash": "abc"})

    def test_roundtrip(self):
        self._seed(self.src)
        entry = self.src.export_session("s1")
        self.assertEqual(len(entry["messages"]), 2)
        self.assertEqual(len(entry["steps"]), 1)
        self.assertEqual(len(entry["files"]), 1)
        new_sid = self.dst.import_session(entry)
        self.assertTrue(new_sid.startswith("imp-"))
        self.assertNotEqual(new_sid, "s1", "导入必须换新 sid，防撞本机主键")
        got = self.dst.get(new_sid)
        self.assertEqual(got["title"], "种子会话")
        self.assertEqual(got["original_task"], "做一件事\n第二行")
        self.assertEqual(got["workspace"], "D:/ws")
        self.assertEqual(got["tag"], "日报")
        msgs = self.dst.messages(new_sid, limit=100)
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant"])
        self.assertEqual(msgs[1]["content"], "好的")
        steps = self.dst.steps(new_sid)
        self.assertEqual(steps[0]["tool"], "read_file")
        self.assertEqual(self.dst.file_events(new_sid)[0]["path"], "a.py")

    def test_live_state_coerced_and_parent_remap(self):
        self._seed(self.src, sid="parent1")
        self._seed(self.src, sid="kid1", state="running", parent="parent1")
        p_entry = self.src.export_session("parent1")
        k_entry = self.src.export_session("kid1")
        parent_map = {"parent1": ""}
        new_parent = self.dst.import_session(p_entry, parent_map)
        parent_map["parent1"] = new_parent
        new_kid = self.dst.import_session(k_entry, parent_map)
        kid = self.dst.get(new_kid)
        self.assertEqual(kid["parent_id"], new_parent, "子会话应挂到本批导入的新父")
        self.assertEqual(kid["state"], "ready", "运行中状态不能随文件迁移（那台机器的进程不在本机）")

    def test_parent_not_in_batch_dangles_to_none(self):
        self._seed(self.src, sid="kid2", parent="elsewhere")
        entry = self.src.export_session("kid2")
        new_sid = self.dst.import_session(entry, {"elsewhere": ""})
        self.assertIsNone(self.dst.get(new_sid)["parent_id"], "批外父会话→置空，不留悬空引用")

    def test_export_missing_returns_none_and_import_rejects_garbage(self):
        self.assertIsNone(self.src.export_session("nope"))
        with self.assertRaises(ValueError):
            self.dst.import_session({"messages": []})
        with self.assertRaises(ValueError):
            self.dst.import_session({"session": {}})
        # 只有 title 也能导入：original_task 用 title 兜底（NOT NULL 列）
        sid = self.dst.import_session({"session": {"title": "x"}})
        self.assertEqual(self.dst.get(sid)["original_task"], "x")
        # 角色不合法的消息被跳过，不影响整批导入
        new_sid = self.dst.import_session({"session": {"id": "o", "title": "t",
                                                       "original_task": "t"},
                                           "messages": [{"role": "hacker", "content": "b"}]})
        self.assertEqual(self.dst.messages(new_sid), [])


if __name__ == "__main__":
    unittest.main()
