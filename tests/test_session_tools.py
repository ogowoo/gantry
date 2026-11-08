# -*- coding: utf-8 -*-
"""DSH 参考工具：会话检索 / 会话读取 / 变更清单 / 子代理控制。"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.session_store import SessionStore  # noqa: E402
from gantry.tools import ToolError, ToolRegistry  # noqa: E402


class SessionSearchStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_sess_"))
        self.store = SessionStore(self.tmp / "agent.db")
        self.store.create("s1", "任务一", str(self.tmp), "m1", title="任务一")
        self.store.add_message("s1", "user", "请修 VPN 连不上的问题", 0)
        self.store.add_message("s1", "assistant", "已定位到证书过期", 0)
        self.store.add_step("s1", 1, 0, {"step": 1, "tool": "read_text", "status": "ok",
                                        "reason": "查 VPN 配置"})

    def test_search_messages(self):
        hits = self.store.search_messages("VPN")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["session_id"], "s1")
        self.assertIn("VPN", hits[0]["snippet"])

    def test_search_steps(self):
        hits = self.store.search_steps("VPN")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["session_id"], "s1")
        self.assertEqual(hits[0]["tool"], "read_text")

    def test_like_wildcards_are_literal(self):
        self.store.add_message("s1", "user", "完成度 100% 了", 0)
        self.assertEqual(self.store.search_messages("100%")[0]["session_id"], "s1")
        self.assertEqual(self.store.search_messages("10_%"), [])


class SessionToolRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_sess_tool_"))
        self.reg = ToolRegistry(self.ws, {})

    def test_tools_registered(self):
        for name in ("session_search", "session_read", "session_changes",
                     "subagent_interrupt", "list_subagent_models", "todo_read"):
            self.assertIn(name, self.reg._tools, f"缺工具 {name}")

    def test_session_search_requires_query(self):
        with self.assertRaises(ToolError):
            asyncio.run(self.reg._tools["session_search"](query=""))


class SessionChangesAggregationTests(unittest.TestCase):
    def test_aggregate_file_events(self):
        import gantry.bridge as b

        fake = SimpleNamespace(
            file_events=lambda sid: [
                {"tool": "write_text", "path": "a.py", "result": "ok", "created": 1},
                {"tool": "replace_text", "path": "a.py", "result": "ok", "created": 2},
                {"tool": "apply_patch", "path": "b.py", "result": "ok", "created": 3},
            ],
            get=lambda sid: {"parent_id": "p", "state": "running"},
        )
        original = b.AGENT_SESSIONS
        b.AGENT_SESSIONS = fake
        try:
            reg = ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_chg_")), {})
            reg.current_session_id = "s1"
            out = asyncio.run(reg._tools["session_changes"]())
        finally:
            b.AGENT_SESSIONS = original
        self.assertEqual(out["total"], 2)
        by_path = {x["path"]: x for x in out["changed"]}
        self.assertEqual(by_path["a.py"]["count"], 2)
        self.assertEqual(by_path["a.py"]["tools"], ["write_text", "replace_text"])

    def test_subagent_interrupt_terminal_is_noop(self):
        import gantry.bridge as b

        fake = SimpleNamespace(
            get=lambda sid: {"parent_id": "p", "state": "completed"},
            patch=lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应 patch")),
        )
        original = b.AGENT_SESSIONS
        b.AGENT_SESSIONS = fake
        try:
            reg = ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_int_")), {})
            out = asyncio.run(reg._tools["subagent_interrupt"](agent_id="sub-1"))
        finally:
            b.AGENT_SESSIONS = original
        self.assertFalse(out["cancelled"])
        self.assertIn("终态", out["note"])

    def test_subagent_interrupt_rejects_non_child(self):
        import gantry.bridge as b

        fake = SimpleNamespace(get=lambda sid: {"parent_id": "", "state": "running"})
        original = b.AGENT_SESSIONS
        b.AGENT_SESSIONS = fake
        try:
            reg = ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_int2_")), {})
            with self.assertRaises(ToolError):
                asyncio.run(reg._tools["subagent_interrupt"](agent_id="s1"))
        finally:
            b.AGENT_SESSIONS = original


if __name__ == "__main__":
    unittest.main()
