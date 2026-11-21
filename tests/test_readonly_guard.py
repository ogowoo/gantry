# -*- coding: utf-8 -*-
"""只读任务在 Agent 层拒绝变更工具。"""
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


class ReadOnlyIntentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="readonly_"))
        self.state = {"state": "ready"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_is_blocked_before_execution(self):
        actions = [
            {"type": "tool", "tool": "write_text", "arguments": {
                "path": "should_not_exist.txt", "content": "x"}},
            {"type": "final", "content": "only analyzed"},
        ]

        async def chat(prompt, model, new):
            return json.dumps(actions.pop(0), ensure_ascii=False)

        runner = AgentRunner(
            ToolRegistry(self.tmp, {}), chat, {"max_total_steps": 8},
            state_getter=lambda: dict(self.state),
            state_saver=lambda **kw: self.state.update(kw),
            step_saver=lambda s: self.state.setdefault("steps", []).append(s),
            file_event_saver=lambda e: None)
        result = asyncio.run(runner.run("read-only analysis of this project", "m"))
        self.assertFalse((self.tmp / "should_not_exist.txt").exists())
        self.assertEqual(result.content, "only analyzed")
        self.assertTrue(any(s.get("status") == "read_only_guard"
                            for s in self.state.get("steps", [])))


if __name__ == "__main__":
    unittest.main()
