# -*- coding: utf-8 -*-
"""限流挂起（suspended）：只有 Copilot 网页模型才挂起；挂起窗口里可换提供商模型继续。"""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.ui_src import ui_js

import gantry.agent as A  # noqa: E402
from gantry.tools import ToolRegistry  # noqa: E402


class SuspendTests(unittest.TestCase):
    def _run(self, exc, model, config=None):
        async def chat(prompt, model=None, force_new=False, **kw):
            raise exc

        saved = []
        cfg = {"max_service_unavailable": 1, "service_retry_base_seconds": 1,
               "service_retry_max_seconds": 1, "service_retry_window_seconds": 1}
        cfg.update(config or {})
        state_box = {"state": "running"}

        def saver(**kw):
            state_box.update(kw)

        runner = A.AgentRunner(
            tools=ToolRegistry(Path(tempfile.mkdtemp(prefix="cbp_susp_")), {}),
            chat=chat, config=cfg,
            state_getter=lambda: dict(state_box),
            state_saver=saver,
            step_saver=lambda st: saved.append(dict(st)),
            file_event_saver=lambda ev: None,
            approval_check=lambda *a, **k: True,
        )
        res = asyncio.run(runner.run("测试挂起", model))
        return res, state_box, saved

    def test_copilot_rate_limit_suspends(self):
        res, state, saved = self._run(A.CopilotRateLimited("rate limited"), "copilot-auto")
        self.assertEqual(str(state.get("state")), "suspended",
                         "Copilot 网页模型限流应挂起，而不是在窗口里空转")
        self.assertEqual(str(state.get("suspend_reason")), "rate_limit")
        self.assertTrue(state.get("retry_resume_at"), "要记下窗口终点，后台据此自动继续")
        self.assertIn("挂起", str(res.content))

    def test_provider_rate_limit_does_not_suspend(self):
        """提供商模型有自己的 429 语义（window_seconds=0）——不挂起。"""
        exc = A.CopilotRateLimited("too many requests")
        exc.window_seconds = 0
        res, state, saved = self._run(exc, "deepseek/deepseek-chat")
        self.assertNotEqual(str(state.get("state")), "suspended",
                            "提供商模型不应进入挂起态")

    def test_suspend_can_be_disabled(self):
        res, state, saved = self._run(A.CopilotRateLimited("rate limited"), "copilot-auto",
                                      {"rate_limit_suspend": False})
        self.assertNotEqual(str(state.get("state")), "suspended")


class SuspendWiringTests(unittest.TestCase):
    def test_state_registered_and_resume_allowed(self):
        from gantry.session_store import SessionStore
        self.assertIn("suspended", SessionStore.STATES, "状态白名单要包含 suspended")

    def test_api_accepts_suspended_resume_and_periodic_task(self):
        api = ((ROOT / "gantry" / "api.py").read_text(encoding="utf-8") + "".join((ROOT / "gantry" / n).read_text(encoding="utf-8") for n in ("routes_core.py", "routes_config.py", "routes_agents.py", "routes_sessions.py")))
        self.assertIn('"suspended"', api, "resume 要接受 suspended")
        self.assertIn("periodic_suspend_resume", api, "要有窗口到点的自动继续巡检")
        self.assertIn("agent.rate_limit_suspend", api, "开关要进配置白名单")

    def test_frontend_offers_provider_swap(self):
        html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
        app = ui_js()
        self.assertIn('id="retrySwap"', html, "挂起遮罩要有换模型入口")
        # 换模型入口已从"下拉 + 确认按钮"改成**按钮列表**（用户要求在全屏遮罩上直接点），
        # 所以按钮是 JS 渲染的：断言动作与参数，而不是断言 HTML 里的 select。
        self.assertIn('data-act="swapModelResume"', app)
        self.assertIn("data-arg=", app)
        self.assertIn("function swapModelResume(", app)
        self.assertIn("/model", app)
        self.assertIn("window.__MODELS", app, "要能筛出 provider/model")
        # 限流提示已改成全页面遮罩：卡片必须在遮罩里，否则又变成挡内容的浮层
        self.assertIn('id="retryMask"', html)
        self.assertIn('class="mask retrymask"', html)


class SyntheticProbeDoesNotResetCountTests(unittest.TestCase):
    """「结果未知只读核对」probe 是本地合成回合（不触碰 Copilot），不能当作
    服务恢复信号：限流与 probe 交替时连续失败计数必须继续累加。

    回归来源：Copilot 每日上限期间 cron 会话每 ~40 秒重试一次，每次都显示
    「第 1 次」——probe 回合的解析成功把 service_failures/retry_attempt 清零，
    退避永远停在 30 秒，max_service_unavailable 也不会触发。
    """

    def test_probe_turn_does_not_reset_retry_count(self):
        PROBE = ('{"type":"tool","tool":"list_directory","arguments":{"path":"."},'
                 '"reason":"上一回合在执行途中被中断，其结果未知：先只读核对。"}')

        class StubRegistry:
            def web_copilot_allowed(self): return False
            def describe(self): return []
            def descriptions(self, only=None): return "[]"
            def has(self, *a, **k): return False
            def names(self): return []
            def execute(self, *a, **k): return {"path": ".", "items": []}

        state_box = {"state": "running"}
        saved_steps = []
        calls = {"n": 0}
        runner_box = {}

        def saver(**kw): state_box.update(kw)

        async def chat(prompt, model=None, force_new=False, **kw):
            calls["n"] += 1
            if calls["n"] > 12:
                raise asyncio.CancelledError()
            if calls["n"] % 2 == 0:
                # 模拟 agentrun 的 probe 分支：合成回合，打标记后返回只读核对动作
                runner_box["r"]._last_turn_synthetic = True
                return PROBE
            raise A.CopilotRateLimited(
                "You've used your daily chat limit. Please try again tomorrow.")

        runner = A.AgentRunner(
            tools=StubRegistry(), chat=chat,
            config={"rate_limit_suspend": False, "max_total_steps": 50},
            state_getter=lambda: dict(state_box), state_saver=saver,
            step_saver=lambda st: saved_steps.append(dict(st)),
            file_event_saver=lambda ev: None,
            approval_check=lambda *a, **k: True,
        )
        runner._system_prompt = lambda: "sys"
        runner._active_tool_names = lambda: []
        runner.service_retry_base_seconds = 0.01
        runner.service_retry_max_seconds = 0.02
        runner_box["r"] = runner

        async def no_sleep(_s):
            return None

        orig_sleep = A.asyncio.sleep
        A.asyncio.sleep = no_sleep
        try:
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(runner.run("测试限流计数", "copilot-auto"))
        finally:
            A.asyncio.sleep = orig_sleep

        errors = [s.get("error", "") for s in saved_steps
                  if s.get("status") == "service_unavailable"]
        self.assertTrue(errors, "限流失败应记录 service_unavailable 步骤")
        self.assertIn("第 1 次", errors[0])
        self.assertTrue(
            any("第 5 次" in e for e in errors),
            f"probe 回合不应清零计数，应看到「第 5 次」：{errors}")
        self.assertGreaterEqual(int(state_box.get("retry_attempt") or 0), 3,
                                "持久化的 retry_attempt 必须随失败累加")


if __name__ == "__main__":
    unittest.main()
