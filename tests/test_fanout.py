# -*- coding: utf-8 -*-
"""确定性扇出：任务归一化、父会话消息、单项失败不影响其余、上限。"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import bridge as b
from gantry import fanout as fanout_mod
from gantry.session_store import SessionStore


class FanoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_fan_"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.saved = (b.AGENT_SESSIONS, b.AGENT_TOOLS.root, b.run_subagent)
        b.AGENT_SESSIONS = SessionStore(self.tmp / "a.db")
        b.AGENT_TOOLS.set_root(self.ws, False)
        self.calls = []
        self.roots = []

        async def fake(task, description="", model="", max_steps=40):
            self.calls.append(task)
            if "任务规划器" in task:
                return {"agent_id": "planner", "steps": 1,
                        "content": '[{"task": "甲", "description": "A"},'
                                   ' {"task": "乙", "description": "B"}]'}
            if "请给父会话写一段简洁汇总" in task:
                return {"agent_id": "summary", "steps": 1, "content": "SUMMARY"}
            self.roots.append(str(b.task_tools().root))
            if "boom" in task:
                raise b.ToolError("模拟失败")
            return {"agent_id": "sub-fake", "steps": 1, "content": "答:" + task}

        b.run_subagent = fake

    def tearDown(self):
        b.AGENT_SESSIONS, b.AGENT_TOOLS.root, b.run_subagent = self.saved

    def test_runs_each_task_and_writes_messages(self):
        out = asyncio.run(fanout_mod.fanout(["任务甲", "任务乙"], model="m"))
        self.assertEqual(out["requested"], 2)
        self.assertEqual(out["succeeded"], 2)
        self.assertEqual(self.calls, ["任务甲", "任务乙"])
        sid = out["session_id"]
        self.assertTrue(sid.startswith("fan-"))
        msgs = b.AGENT_SESSIONS.messages(sid)
        self.assertEqual(msgs[0]["role"], "user")
        self.assertIn("任务甲", msgs[0]["content"])
        self.assertEqual([m["role"] for m in msgs[1:]], ["assistant", "assistant"])
        self.assertTrue(b.AGENT_SESSIONS.get(sid)["parent_id"] in (None, "", sid))

    def test_failure_does_not_stop_others(self):
        out = asyncio.run(fanout_mod.fanout(["boom", "任务乙"], model="m"))
        self.assertEqual((out["succeeded"], out["failed"]), (1, 1))
        self.assertFalse(out["results"][0]["ok"])
        self.assertIn("模拟失败", out["results"][0]["error"])
        self.assertTrue(out["results"][1]["ok"])

    def test_accepts_dict_items(self):
        out = asyncio.run(fanout_mod.fanout([{"task": "甲", "description": "标签"}], model="m"))
        self.assertEqual(out["succeeded"], 1)

    def test_rejects_empty_and_too_many(self):
        with self.assertRaises(b.ToolError):
            asyncio.run(fanout_mod.fanout([], model="m"))
        with self.assertRaises(b.ToolError):
            asyncio.run(fanout_mod.fanout([f"t{i}" for i in range(9)], model="m"))

    def test_workspace_root_restored(self):
        other = self.tmp / "other"
        other.mkdir()
        before = Path(b.AGENT_TOOLS.root)
        asyncio.run(fanout_mod.fanout(["任务甲"], model="m", workspace=str(other)))
        self.assertEqual(Path(b.AGENT_TOOLS.root), before)

    def test_rejects_duplicate_tasks(self):
        with self.assertRaises(b.ToolError):
            asyncio.run(fanout_mod.fanout(["甲", "甲"], model="m"))

    def test_depends_on_order_and_cycle(self):
        out = asyncio.run(fanout_mod.fanout(
            [{"task": "甲"}, {"task": "乙", "depends_on": [1]}], model="m"))
        self.assertEqual(self.calls, ["甲", "乙"])
        self.assertEqual(out["succeeded"], 2)
        with self.assertRaises(b.ToolError):
            asyncio.run(fanout_mod.fanout(
                [{"task": "甲", "depends_on": [2]},
                 {"task": "乙", "depends_on": [1]}], model="m"))

    def test_serial_disables_parallel(self):
        out = asyncio.run(fanout_mod.fanout(
            ["甲", "乙"], model="deepseek/x", serial=True))
        self.assertFalse(out["parallel"])
        self.assertTrue(out["serial"])
        self.assertEqual(self.calls, ["甲", "乙"])

    def test_isolate_workspace_roots(self):
        out = asyncio.run(fanout_mod.fanout(
            ["甲", "乙"], model="m", isolate_workspace=True))
        self.assertTrue(out["isolated"])
        self.assertEqual(len(self.roots), 2)
        self.assertNotEqual(self.roots[0], self.roots[1])
        self.assertTrue(all(".fanout" in r for r in self.roots))

    def test_summary_written(self):
        out = asyncio.run(fanout_mod.fanout(["甲", "乙"], model="m", summary=True))
        msgs = b.AGENT_SESSIONS.messages(out["session_id"])
        self.assertTrue(any("汇总" in str(m.get("content") or "") for m in msgs))

    def test_model_split_goal(self):
        out = asyncio.run(fanout_mod.fanout(
            tasks=[], goal="做两件事", split="model", model="m"))
        self.assertEqual(out["requested"], 2)
        self.assertIn("任务规划器", self.calls[0])
        self.assertEqual(out["succeeded"], 2)


if __name__ == "__main__":
    unittest.main()
