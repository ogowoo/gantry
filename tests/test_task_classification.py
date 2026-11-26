# -*- coding: utf-8 -*-
"""任务分型规则的回归：执行任务不得被转译成"写脚本"，完成标准是交付物本身。

实测事故：定时任务「生成当日要闻/财经 PDF 报告」被跑成开发任务——Agent 用
copilot_file 生成一个 2.2KB 脚本骨架，validate_python 通过后就宣布完成；
新闻没抓、PDF 没生成、任务要求的 _failure.txt 也没写，会话却 state=completed。
协议里补了「任务分型与完成判定」段，本文件守住它的存在与中英一致。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import prompt  # noqa: E402
from gantry import prompt_lang  # noqa: E402


class TaskClassificationRuleTests(unittest.TestCase):
    def test_zh_rule_present(self):
        zh = prompt.AGENT_PROTOCOL
        self.assertIn("任务分型", zh)
        self.assertIn("执行任务", zh)
        self.assertIn("不要转译成", zh, "执行任务不能被改写成写脚本的工程任务")
        self.assertIn("交付物真实存在", zh, "完成标准必须是交付物本身")
        self.assertIn("不得宣称完成", zh, "关键交付物未产出时禁止宣称完成")

    def test_zh_validation_gate_still_scoped_to_code_tasks(self):
        """验证闭环（validate_python/run_unittest）只属于治理/代码任务的完成门。"""
        zh = prompt.AGENT_PROTOCOL
        self.assertIn("治理/代码任务", zh)
        self.assertIn("治理或代码修改任务必须形成验证闭环", zh,
                      "代码任务的验证闭环规则不能被冲掉")

    def test_en_translation_present(self):
        en = prompt_lang.EN.get("protocol") or ""
        self.assertIn("Task classification", en)
        self.assertIn("execution task", en)
        self.assertIn("generator script", en)
        self.assertIn("failure protocol", en)
        self.assertIn("do not claim completion", en)

    def test_numeric_constants_still_match(self):
        """新增段落后中英文协议的数字常量仍不能漂移。"""

        def nums(text):
            return sorted(int(x) for x in re.findall(
                r"(?<![A-Za-z0-9_.])\d{2,}(?![A-Za-z0-9_])", text))

        self.assertEqual(nums(prompt.AGENT_PROTOCOL),
                         nums(prompt_lang.EN.get("protocol") or ""))


if __name__ == "__main__":
    unittest.main()
