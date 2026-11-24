"""会话发起模式（agent / chat）落库与回放。

回归的 bug：新任务选「普通对话」，进入会话后界面变成「Agent 继续执行」，
下一句跟进的 `agent_mode=true` 把纯问答变成了 Agent 任务——因为创建时的
模式只存在于请求里，从未落库。
"""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from gantry import bridge as b
from gantry.session_store import SessionStore


class ModeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_mode_"))
        self.store = SessionStore(self.tmp / "a.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_is_agent(self):
        self.store.create("a1", "t", str(self.tmp), "m")
        self.assertEqual(self.store.get("a1")["mode"], "agent")

    def test_create_chat(self):
        self.store.create("a2", "t", str(self.tmp), "m", mode="chat")
        self.assertEqual(self.store.get("a2")["mode"], "chat")

    def test_create_normalizes_junk(self):
        self.store.create("a3", "t", str(self.tmp), "m", mode="  CHAT ")
        self.assertEqual(self.store.get("a3")["mode"], "chat")
        self.store.create("a4", "t", str(self.tmp), "m", mode="whatever")
        self.assertEqual(self.store.get("a4")["mode"], "agent")

    def test_set_mode_roundtrip(self):
        self.store.create("a5", "t", str(self.tmp), "m")
        self.store.set_mode("a5", "chat")
        self.assertEqual(self.store.get("a5")["mode"], "chat")
        self.store.set_mode("a5", "agent")
        self.assertEqual(self.store.get("a5")["mode"], "agent")

    def test_light_list_exposes_mode_and_persona(self):
        self.store.create("a6", "t", str(self.tmp), "m", persona="terse", mode="chat")
        row = [x for x in self.store.list(light=True) if x["id"] == "a6"][0]
        self.assertEqual(row["mode"], "chat")
        self.assertEqual(row["persona"], "terse")

    def test_create_is_idempotent_keeps_mode(self):
        self.store.create("a7", "t", str(self.tmp), "m", mode="chat")
        self.store.create("a7", "t2", str(self.tmp), "m2")   # INSERT OR IGNORE
        self.assertEqual(self.store.get("a7")["mode"], "chat")


class _RouteBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_modeapi_"))
        self.saved = (b.AGENT_SESSIONS, b.enqueue_session_message,
                      getattr(b.AGENT_TOOLS, "root", None))
        b.AGENT_SESSIONS = SessionStore(self.tmp / "a.db")
        b.AGENT_TOOLS.root = self.tmp
        self.sent = []

        async def fake_enqueue(sid, content, model_id, agent_mode, attach_paths, msg_id=None):
            self.sent.append(agent_mode)
            return {"queued": True, "position": 1}

        b.enqueue_session_message = fake_enqueue

    def tearDown(self):
        (b.AGENT_SESSIONS, b.enqueue_session_message,
         root) = self.saved
        b.AGENT_TOOLS.root = root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _post(self, sid, mode_agent: bool):
        return self.c.post(f"/v1/agent/sessions/{sid}/messages",
                           json={"content": "今天深圳的天气怎样？",
                                 "model": "copilot-auto",
                                 "agent_mode": mode_agent})


class RouteModeTests(_RouteBase):
    def test_chat_message_records_chat_mode(self):
        b.AGENT_SESSIONS.create("c1", "t", str(self.tmp), "copilot-auto")
        r = self._post("c1", False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(b.AGENT_SESSIONS.get("c1")["mode"], "chat")

    def test_agent_message_records_agent_mode(self):
        b.AGENT_SESSIONS.create("c2", "t", str(self.tmp), "copilot-auto", mode="chat")
        r = self._post("c2", True)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(b.AGENT_SESSIONS.get("c2")["mode"], "agent")

    def test_mode_flag_passed_through_unchanged(self):
        b.AGENT_SESSIONS.create("c3", "t", str(self.tmp), "copilot-auto")
        self._post("c3", False)
        self._post("c3", True)
        self.assertEqual(self.sent, [False, True])

    def test_continue_endpoint_forces_agent(self):
        b.AGENT_SESSIONS.create("c4", "t", str(self.tmp), "copilot-auto", mode="chat")
        r = self.c.post("/v1/agent/sessions/c4/continue",
                        json={"content": "继续", "model": "copilot-auto",
                              "agent_mode": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(b.AGENT_SESSIONS.get("c4")["mode"], "agent")

    def test_unknown_session_404_leaves_store_alone(self):
        r = self._post("nope-xyz", False)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.sent, [])


class PlainCompletionsModeTests(unittest.TestCase):
    """普通对话入口创建会话时必须写 mode=chat（此前只写请求标志）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_plain_"))
        from gantry import agentrun as ar
        self.ar = ar
        self.saved = (b.AGENT_SESSIONS, ar.plain_chat_turn, b.AGENT_WORKSPACE,
                      b.save_image_attachments, b.normalize_model)
        b.AGENT_SESSIONS = SessionStore(self.tmp / "a.db")
        b.AGENT_WORKSPACE = self.tmp
        b.save_image_attachments = lambda x: []
        b.normalize_model = lambda m: m or "copilot-auto"

        async def fake_turn(sid, content, model_id, attach_paths=None):
            return {"content": "晴，26 度"}

        # plain_completions 调的是本模块的全局名，必须打在 agentrun 上
        ar.plain_chat_turn = fake_turn

    def tearDown(self):
        (b.AGENT_SESSIONS, ar_turn, b.AGENT_WORKSPACE,
         b.save_image_attachments, b.normalize_model) = self.saved
        self.ar.plain_chat_turn = ar_turn
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plain_task_is_chat(self):
        req = b.AgentRequest(model="copilot-auto",
                             messages=[{"role": "user", "content": "今天深圳的天气怎样？"}],
                             session_id="pc1")
        out = asyncio.run(b.plain_completions(req))
        self.assertEqual(out["mode"], "chat")
        self.assertEqual(b.AGENT_SESSIONS.get("pc1")["mode"], "chat")

    def test_reused_agent_session_flips_to_chat(self):
        b.AGENT_SESSIONS.create("pc2", "旧任务", str(self.tmp), "copilot-auto")
        req = b.AgentRequest(model="copilot-auto",
                             messages=[{"role": "user", "content": "随便聊聊"}],
                             session_id="pc2")
        asyncio.run(b.plain_completions(req))
        self.assertEqual(b.AGENT_SESSIONS.get("pc2")["mode"], "chat")


if __name__ == "__main__":
    unittest.main()
