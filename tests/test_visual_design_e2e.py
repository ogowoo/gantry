# -*- coding: utf-8 -*-
"""visual-design Agent 端到端：1920×1080 中文海报，六个工具全部走 Agent 循环。"""
import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gantry.agent import AgentRunner
from gantry.plugins import PluginHost
from gantry.tools import ToolRegistry

PID = "e2e-poster-1920"
HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;width:1920px;height:1080px;background:#f5fbff}
h1{font-family:"Microsoft YaHei";font-size:170px;color:#e85d75;
text-align:center;margin-top:300px}</style></head><body>
<h1>中文宣传海报</h1></body></html>"""


class VisualDesignAgentE2ETests(unittest.TestCase):
    def test_agent_creates_1920x1080_chinese_poster(self):
        ws = Path(tempfile.mkdtemp(prefix="vd_e2e_"))
        try:
            reg = ToolRegistry(ws, {"max_total_steps": 20})
            host = PluginHost(ROOT, registry=reg)
            host.load("visual-design")
            actions = [
                {"type": "tool", "tool": "design_create",
                 "arguments": {"project_id": PID, "title": "中文宣传海报",
                               "width": 1920, "height": 1080}, "reason": "create"},
                {"type": "tool", "tool": "design_modify",
                 "arguments": {"project_id": PID, "target": "html",
                               "mode": "replace", "content": HTML}, "reason": "html"},
                {"type": "tool", "tool": "design_render",
                 "arguments": {"project_id": PID, "target": "html"}, "reason": "render"},
                {"type": "tool", "tool": "design_preview",
                 "arguments": {"project_id": PID, "target": "html",
                               "max_side": 512}, "reason": "preview"},
                {"type": "tool", "tool": "design_inspect",
                 "arguments": {"project_id": PID}, "reason": "inspect"},
                {"type": "tool", "tool": "design_export",
                 "arguments": {"project_id": PID, "format": "png"}, "reason": "export"},
                {"type": "final", "content": "完成"},
            ]
            idx = 0

            async def chat(prompt, model, make_new):
                nonlocal idx
                msg = actions[min(idx, len(actions) - 1)]
                idx += 1
                return json.dumps(msg, ensure_ascii=False)

            state = {"state": "ready"}
            steps = []
            runner = AgentRunner(
                reg, chat, {"max_total_steps": 20},
                state_getter=lambda: dict(state),
                state_saver=lambda **kw: state.update(kw),
                step_saver=lambda s: steps.append(s),
                file_event_saver=lambda e: None)
            result = asyncio.run(runner.run("制作一张 1920x1080 的中文宣传海报", "m"))
            self.assertEqual(result.content, "完成")
            self.assertEqual([s["tool"] for s in steps], [
                "design_create", "design_modify", "design_render",
                "design_preview", "design_inspect", "design_export"])
            self.assertTrue(all(s["status"] == "ok" for s in steps))
            png = ws / "visual-projects" / PID / "output" / "design.png"
            self.assertTrue(png.is_file())
            from PIL import Image
            with Image.open(png) as im:
                self.assertEqual(im.size, (1920, 1080))
            self.assertTrue((ws / "visual-projects" / PID / "source" / "index.html").is_file())
            host.unload("visual-design")
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
