# -*- coding: utf-8 -*-
"""AUD-P4：任务完成后清理 Copilot 会话（保留本地记录）。

- 总结失败 → 中止删除，远端映射原样保留；
- 成功 → 先落 task_summary 与总结消息，再对账式删除并清空 URL 映射；
- 无远端 URL → 拒绝并给出明确提示。
"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 注意导入顺序：bridge 在模块尾 import agentrun，必须先导入 bridge
from gantry import bridge as b  # noqa: E402
from gantry import agentrun  # noqa: E402
from gantry import copilot_delete as cd  # noqa: E402
from gantry.session_store import SessionStore  # noqa: E402


def _copilot_url(n: int) -> str:
    return f"https://copilot.cloud.microsoft/chat/conversation/conv{n:08d}"


class FakeWindow:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self, None

    async def __aexit__(self, *exc):
        return False


class CopilotCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_clean_"))
        self.saved = (b.AGENT_SESSIONS, b.session_running)
        self.store = SessionStore(self.tmp / "a.db")
        b.AGENT_SESSIONS = self.store
        self.sid = self.store.create(
            "task-clean1", "任务", str(self.tmp), "copilot-auto")["id"]
        self.store.patch(self.sid, conversation_urls=[_copilot_url(1), _copilot_url(2)],
                         current_url=_copilot_url(1), state="completed")

    def tearDown(self):
        b.AGENT_SESSIONS, b.session_running = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch_runtime(self, chat_fn, delete_fn, finalize_fn):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(b, "session_running", return_value=False))
        stack.enter_context(mock.patch.object(
            b, "_window", side_effect=lambda *a, **k: FakeWindow(None)))
        stack.enter_context(mock.patch.object(b, "_forget_selected_model"))
        stack.enter_context(mock.patch.object(agentrun, "agent_chat_once",
                                              side_effect=chat_fn))
        stack.enter_context(mock.patch.object(cd, "delete_copilot_conversation",
                                              side_effect=delete_fn))
        stack.enter_context(mock.patch.object(cd, "finalize_session_remote_deletion",
                                              side_effect=finalize_fn))
        return stack

    def test_no_remote_urls_rejected(self):
        self.store.patch(self.sid, conversation_urls=[], current_url=None)
        with self._patch_runtime(chat_fn=lambda *a, **k: "总结",
                                 delete_fn=lambda *a, **k: {"deleted": True},
                                 finalize_fn=lambda *a, **k: []):
            out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertFalse(out["ok"])
        self.assertIn("没有可清理", out["error"])

    def test_summary_failure_aborts_without_delete(self):
        async def chat(prompt, model, make_new):
            raise RuntimeError("You've used your daily chat limit")

        with self._patch_runtime(chat_fn=chat, delete_fn=lambda *a, **k: None,
                                 finalize_fn=lambda *a, **k: []):
            out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertFalse(out["ok"])
        self.assertIn("总结失败", out["error"])
        st = self.store.get(self.sid)
        self.assertEqual(len(st["conversation_urls"]), 2,
                         "总结失败时不得动远端映射")

    def test_success_summarizes_deletes_and_keeps_local(self):
        calls = []

        async def chat(prompt, model, make_new):
            calls.append(prompt)
            return "目标：审核整改。产物：发布 beta.41。遗留：三个实弹测试。"

        def delete(url, marker):
            calls.append(("delete", url, marker))
            return {"url": url, "deleted": True}

        def finalize(sid, results):
            return results

        with self._patch_runtime(chat_fn=chat, delete_fn=delete, finalize_fn=finalize):
            out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertTrue(out["ok"], out)
        self.assertTrue(out["summarized"])
        self.assertEqual(out["deleted"], 2)
        st = self.store.get(self.sid)
        self.assertEqual(st["conversation_urls"], [])
        self.assertIsNone(st["current_url"])
        self.assertIn("审核整改", st.get("task_summary") or "")
        msgs = self.store.messages(self.sid) or []
        self.assertTrue(any("【任务总结】" in m["content"] for m in msgs),
                        "总结必须作为助手消息落库（本地记录保留）")


if __name__ == "__main__":
    unittest.main()
