# -*- coding: utf-8 -*-
"""Agent 领域 profile：关键词路由、强制 profile、提示词语言和工具组。"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.agent_profiles import (profile_for_task, profile_groups,
                                   profile_prompt)
from gantry.agent import AgentRunner
from gantry.tools import ToolRegistry


class ProfileRouteTests(unittest.TestCase):
    def test_power_automate(self):
        p = profile_for_task("帮我做一个 Power Automate 云端流，审批后发邮件")
        self.assertEqual(p.name, "power_automate")
        self.assertIn("power_automate", profile_groups(p))

    def test_power_apps(self):
        p = profile_for_task("创建一个 canvas app 画布应用")
        self.assertEqual(p.name, "power_apps")
        # Power Apps 工具由可选插件动态注册，不放进静态 TOOL_GROUPS；
        # 未分组工具本来就是 core，插件加载后仍可见。
        self.assertEqual(profile_groups(p), ())

    def test_office(self):
        p = profile_for_task("把这个 Excel 表格更新后导出报表")
        self.assertEqual(p.name, "office365")
        self.assertIn("office", profile_groups(p))

    def test_coding(self):
        p = profile_for_task("修复这个项目的 Python 测试")
        self.assertEqual(p.name, "coding")
        self.assertIn("python", profile_groups(p))

    def test_generic_has_no_profile(self):
        self.assertIsNone(profile_for_task("今天天气怎么样"))

    def test_visual(self):
        p = profile_for_task("用 GIMP 做一张六一儿童节海报")
        self.assertEqual(p.name, "visual")
        self.assertIn("poster-design", p.zh)
        self.assertIn("visual design", profile_prompt(p, "en").lower())

    def test_forced_and_off(self):
        self.assertEqual(profile_for_task("随便", {"profile": "office365"}).name,
                         "office365")
        self.assertIsNone(profile_for_task("写一个 python 脚本", {"profile": "off"}))

    def test_prompt_language(self):
        p = profile_for_task("Power Apps canvas app")
        self.assertIn("Power Apps", profile_prompt(p, "en"))
        self.assertNotIn("本任务按", profile_prompt(p, "en"))
        self.assertIn("Power Apps", profile_prompt(p, "zh"))


class PromptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="profile_prompt_"))

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def _runner(self, task):
        cfg = {"max_total_steps": 10}
        r = AgentRunner(ToolRegistry(self.ws, cfg), lambda *a, **k: None, cfg,
                        state_getter=lambda: {"state": "ready"},
                        state_saver=lambda **k: None, step_saver=lambda s: None,
                        file_event_saver=lambda e: None)
        r._task_text = task
        return r

    def test_system_prompt_contains_profile(self):
        text = self._runner("把 word 文档转成 pdf")._system_prompt()
        self.assertIn("Office 365", text)
        self.assertIn("office365", text)

    def test_generic_prompt_has_no_profile(self):
        text = self._runner("今天天气怎么样")._system_prompt()
        self.assertNotIn("本任务按", text)
        self.assertNotIn("This task uses", text)


if __name__ == "__main__":
    unittest.main()
