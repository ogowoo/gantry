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

    def test_partial_failure_keeps_failed_url_index(self):
        """部分删除失败：失败条目的 URL 索引必须保留，绝不整体清空（审核 2026-11-26）。"""
        def delete(url, marker):
            return {"url": url, "deleted": url.endswith("conv00000001")}

        def finalize(sid, results):
            return results

        with self._patch_runtime(chat_fn=lambda *a, **k: "总结文本",
                                 delete_fn=delete, finalize_fn=finalize):
            out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("error"), "部分失败必须带 error 文案（UI toast 不再空白）")
        self.assertEqual(out["deleted"], 1)
        st = self.store.get(self.sid)
        self.assertEqual(st["conversation_urls"], [_copilot_url(2)],
                         "失败条目的索引必须保留，可重试")
        self.assertIsNone(st["current_url"], "current_url 指向已删会话时必须置空")
        self.assertEqual(st["cleanup_suggested"], 1, "部分失败保留 🧹 建议，可重试")

    def test_running_recheck_after_summary_aborts(self):
        """总结期间会话重新跑起来 → 中止删除，映射原样保留（审核 2026-11-26）。"""
        running = {"v": False}

        async def chat(prompt, model, make_new):
            running["v"] = True      # 模拟总结期间会话被重新拉起
            return "总结文本"

        with self._patch_runtime(chat_fn=chat, delete_fn=lambda *a, **k: None,
                                 finalize_fn=lambda *a, **k: []):
            with mock.patch.object(b, "session_running",
                                   side_effect=lambda s: running["v"]):
                out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertFalse(out["ok"])
        self.assertIn("重新开始运行", out["error"])
        st = self.store.get(self.sid)
        self.assertEqual(len(st["conversation_urls"]), 2, "中止时不得动远端映射")

    def test_cleanup_keeps_existing_task_plan(self):
        """已有任务图（dict）时清理总结不得覆盖它（_work_ledger 消费 dict）。"""
        self.store.patch(self.sid, task_summary={"plan_id": "p1", "status": "ready"})
        with self._patch_runtime(chat_fn=lambda *a, **k: "总结文本",
                                 delete_fn=lambda *a, **k: {"url": a[0], "deleted": True},
                                 finalize_fn=lambda *a, **k: []):
            out = asyncio.run(agentrun.cleanup_copilot_conversation(self.sid))
        self.assertTrue(out["ok"], out)
        st = self.store.get(self.sid)
        self.assertEqual(st["task_summary"], {"plan_id": "p1", "status": "ready"},
                         "任务图不能被清理总结文本覆盖")


class CleanupLedgerGuardTests(unittest.TestCase):
    """_work_ledger 对文本型 task_summary 的类型守卫（审核 2026-11-26）。"""

    def test_work_ledger_tolerates_text_summary(self):
        from gantry.agent import AgentRunner
        from gantry.tools import ToolRegistry

        ws = Path(tempfile.mkdtemp(prefix="cbp_ledger_"))
        try:
            r = AgentRunner(ToolRegistry(ws, {}), lambda *a, **k: None, {},
                            state_getter=lambda: {}, state_saver=lambda **k: None,
                            step_saver=lambda s: None, file_event_saver=lambda e: None)
            ledger = r._work_ledger({"task_summary": "纯文本清理总结"}, [])
            self.assertNotIn("任务图", ledger, "文本总结不是任务图，不能当 dict 消费")
            ledger2 = r._work_ledger(
                {"task_summary": {"plan_id": "p1", "status": "ready"}}, [])
            self.assertEqual(ledger2["任务图"], {"plan_id": "p1", "status": "ready"})
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
