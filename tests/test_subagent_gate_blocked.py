# -*- coding: utf-8 -*-
"""H1 回归：runner 失败出口（gate_blocked 等）返回后，_drive_child 不得把
子会话状态覆盖回 completed，必须把失败原因透传为 error（fanout 以 error 判 ok）。

审计 C12 也要求补真实 _drive_child 路径的测试（此前 fanout 测试全部 stub run_subagent）。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import bridge as b
from gantry.agent import AgentResult
from gantry.session_store import SessionStore


class _GateBlockedRunner:
    """复刻 agent.py 的 gate_blocked 失败出口：返回**之前**已通过 state_saver
    （即 _drive_child 传入的 save 回调）把子会话置 failed。"""

    def __init__(self, reg, turn, cfg, get, save, add_step, add_file, add_conv_stat, **kw):
        self._save = save

    async def run(self, task, model):
        self._save(state="failed", total_steps=4,
                   last_error="验证门连续 3 次未通过：测试失败。已停止自动重试以避免无限循环。")
        return AgentResult("验证门被拒，任务未完成",
                           [{"step": 4, "status": "gate_blocked"}])


class _OkRunner:
    def __init__(self, reg, turn, cfg, get, save, add_step, add_file, add_conv_stat, **kw):
        self._save = save

    async def run(self, task, model):
        # 与真实 runner 的成功出口一致：返回前先落 state=completed
        # （AUD-P1-03 后 _drive_child 只认显式 completed 为成功）。
        self._save(state="completed", total_steps=2)
        return AgentResult("任务已完成", [{"step": 1, "status": "ok", "tool": "read_text"}])


class SubagentGateBlockedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_subfail_"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.saved = (b.AGENT_SESSIONS, b.AGENT_TOOLS.root, b.AgentRunner, dict(b.PROVIDERS))
        b.AGENT_SESSIONS = SessionStore(self.tmp / "a.db")
        b.AGENT_TOOLS.set_root(self.ws, False)
        # 走 provider 通道：_drive_child 不会去抢自动化窗口（测试里没有浏览器）
        b.PROVIDERS["fakeprov"] = object()

    def tearDown(self):
        b.AGENT_SESSIONS, root, runner, provs = self.saved
        b.AGENT_TOOLS.set_root(root, False)
        b.AgentRunner = runner
        b.PROVIDERS.clear()
        b.PROVIDERS.update(provs)

    def test_gate_blocked_child_reported_as_failure(self):
        """验证门被拒（runner 失败出口）→ fanout 结果 ok=false、子会话 state=failed。"""
        b.AgentRunner = _GateBlockedRunner
        out = asyncio.run(b.run_subagent("写代码并跑通验证", model="fakeprov/m1"))
        self.assertTrue(out.get("error"),
                        "runner 失败出口必须把 error 透传——fanout/orchestrator 以 error 判 ok")
        self.assertIn("验证门", out["error"])
        st = b.AGENT_SESSIONS.get(out["agent_id"]) or {}
        self.assertEqual(st.get("state"), "failed",
                         "_drive_child 不得把 runner 已落的 failed 覆盖回 completed")

    def test_successful_child_still_completes(self):
        b.AgentRunner = _OkRunner
        out = asyncio.run(b.run_subagent("读一个文件", model="fakeprov/m1"))
        self.assertIsNone(out.get("error"))
        st = b.AGENT_SESSIONS.get(out["agent_id"]) or {}
        self.assertEqual(st.get("state"), "completed")


if __name__ == "__main__":
    unittest.main()
