# -*- coding: utf-8 -*-
"""AgentRunner final 必须被任务图验收门拦住。"""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.agent import AgentRunner
from gantry.tools import ToolRegistry


class FinalGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="final_gate_"))
        self.state = {"state": "ready"}
        self.steps = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_repeated_final_is_blocked_until_limit(self):
        async def chat(prompt, model, new):
            return json.dumps({"type": "final", "content": "claimed done"})

        runner = AgentRunner(
            ToolRegistry(self.tmp, {}), chat, {"max_total_steps": 10},
            state_getter=lambda: dict(self.state),
            state_saver=lambda **kw: self.state.update(kw),
            step_saver=lambda s: self.steps.append(s),
            file_event_saver=lambda e: None,
            final_gate=lambda content: {"ok": False, "reason": "tasks incomplete"})
        result = asyncio.run(runner.run("analyze project", "m"))
        self.assertEqual(self.state.get("state"), "failed")
        self.assertIn("验收", result.content)
        self.assertTrue(any(s.get("status") == "task_gate" for s in self.steps))
        self.assertTrue(any(s.get("status") == "task_gate_blocked" for s in self.steps))


if __name__ == "__main__":
    unittest.main()
