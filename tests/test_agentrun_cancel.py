# -*- coding: utf-8 -*-
"""run_agent 的 CancelledError 收尾：回合被取消（客户端断开/服务停机）时，
会话必须落库 cancelled + last_error，而不是永久停在 running（auto_resume 捞不到）。
"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import bridge as b
from gantry.session_store import SessionStore
from gantry.snapshots import SnapshotStore


class _SlowProvider:
    async def complete(self, model, messages, response_schema=None):
        await asyncio.sleep(30)
        return '{"type":"final","content":"不会到达"}'


class RunAgentCancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_cancel_"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.saved = (b.AGENT_SESSIONS, b.SNAPSHOTS, b.AGENT_TOOLS.root, dict(b.PROVIDERS))
        b.AGENT_SESSIONS = SessionStore(self.tmp / "agent.db")
        b.SNAPSHOTS = SnapshotStore(self.tmp / "snaps")
        b.AGENT_TOOLS.set_root(self.ws, False)

    def tearDown(self):
        b.AGENT_SESSIONS, b.SNAPSHOTS, _r, provs = self.saved
        b.PROVIDERS.clear()
        b.PROVIDERS.update(provs)

    def test_cancel_persists_cancelled_state_and_reraises(self):
        b.PROVIDERS["fakeprov"] = _SlowProvider()
        req = b.AgentRequest(model="fakeprov/m1",
                             messages=[b.Message(role="user", content="跑一个任务")],
                             stream=False, session_id="s1", workspace=str(self.ws))

        async def main():
            task = asyncio.ensure_future(b.run_agent(req))
            await asyncio.sleep(0.5)          # 等回合进入 provider 调用
            task.cancel()
            try:
                await task
                return "no-raise"
            except asyncio.CancelledError:
                return "cancelled"

        self.assertEqual(asyncio.run(main()), "cancelled", "取消语义必须原样上抛")
        st = b.AGENT_SESSIONS.get("s1") or {}
        self.assertEqual(st.get("state"), "cancelled",
                         "被取消的回合不能永久停在 running（auto_resume 只捞 paused）")
        self.assertTrue(st.get("last_error"), "要落库失败原因")


if __name__ == "__main__":
    unittest.main()
