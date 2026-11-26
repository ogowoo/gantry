# -*- coding: utf-8 -*-
"""工作区打包注入（AUD-P5）：新任务首轮把工作区文本文件合并成一个 txt 给 Copilot。

用户需求：新会话时如果工作区有文件，打包成 txt 一次丢给 Copilot 分析，
省掉模型逐个 read_text 的回合（省会话）。

约束：
- 只收文本类文件（复用 fileview.TEXT_EXT），二进制/隐藏文件/噪声目录跳过；
- 逐文件上限、文件数上限、总字符上限可配且默认保守；截断必须显式告知模型；
- 只进首轮用户消息，不进系统提示词（prompt cache 依赖逐字稳定）；
- 仅 Copilot 网页通道注入；自升级任务跳过（目标在安装目录，与工作区无关）。
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _mk_ws(files: dict[str, str] | None = None, dirs: list[str] | None = None) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cbp_bundle_"))
    for d in (dirs or []):
        (ws / d).mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return ws


class BundleBuildTests(unittest.TestCase):
    def test_text_files_included_binary_hidden_noise_skipped(self):
        from gantry.workspace_bundle import build_workspace_bundle

        ws = _mk_ws({
            "a.txt": "hello a",
            "sub/b.py": "x = 1\n",
            "sub/img.png": "\x00\x01binary",
            ".hidden.txt": "secret",
            "node_modules/pkg/index.js": "noise",
            "README.md": "# readme",
        })
        body, stats = build_workspace_bundle(ws)
        self.assertTrue(body.startswith("【工作区文件打包】"))
        self.assertIn("a.txt", body)
        self.assertIn("sub/b.py", body)
        self.assertIn("README.md", body)
        self.assertNotIn("img.png", body)
        self.assertNotIn("hidden", body)
        self.assertNotIn("node_modules", body)
        self.assertEqual(stats["files"], 3)
        self.assertFalse(stats["truncated"])
        self.assertIn("### 文件: a.txt", body, "每个文件带相对路径头")

    def test_missing_root_returns_empty(self):
        from gantry.workspace_bundle import build_workspace_bundle

        body, stats = build_workspace_bundle(ws := _mk_ws() / "no-such-dir")
        self.assertEqual(body, "")
        self.assertEqual(stats["files"], 0)

    def test_max_files_truncation(self):
        from gantry.workspace_bundle import build_workspace_bundle

        ws = _mk_ws({f"f{i}.txt": f"content {i}" for i in range(5)})
        body, stats = build_workspace_bundle(ws, max_files=2)
        self.assertTrue(stats["truncated"])
        self.assertEqual(stats["files"], 2)
        self.assertNotIn("f2.txt", body)

    def test_max_chars_truncation(self):
        from gantry.workspace_bundle import build_workspace_bundle

        ws = _mk_ws({"a.txt": "x" * 500, "b.txt": "y" * 500})
        # 第一个块（含文件头约 530 字符）放得下，第二个放不下
        body, stats = build_workspace_bundle(ws, max_chars=600)
        self.assertTrue(stats["truncated"])
        self.assertIn("a.txt", body)
        self.assertNotIn("b.txt", body)

    def test_per_file_cap_marked(self):
        from gantry.workspace_bundle import build_workspace_bundle

        # 字节上限是 max_per_file*4：300 字节的 ASCII 文件不会被当二进制跳过，
        # 但字符数 300 > 100，应触发逐文件截断标记。
        ws = _mk_ws({"long.txt": "z" * 300})
        body, stats = build_workspace_bundle(ws, max_per_file=100)
        self.assertIn("文件过长，已截断", body)
        self.assertFalse(stats["truncated"], "单文件截断不算打包截断")

    def test_oversize_binary_bytes_skipped(self):
        """字节数超过 max_per_file*4 的文件按二进制跳过（UTF-8 中文 3 字节余量）。"""
        from gantry.workspace_bundle import build_workspace_bundle

        ws = _mk_ws({"small.txt": "ok"})
        (ws / "big.txt").write_bytes(b"1" * 5000)
        body, stats = build_workspace_bundle(ws, max_per_file=100)
        self.assertIn("small.txt", body)
        self.assertNotIn("big.txt", body)
        self.assertEqual(stats["files"], 1)


class BundleTextGatingTests(unittest.TestCase):
    def test_enabled_web_includes_header(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({"a.txt": "hello"})
        text = workspace_bundle_text(ws, {}, web_allowed=True)
        self.assertIn("【工作区文件打包】", text)
        self.assertIn("a.txt", text)

    def test_disabled_by_config(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({"a.txt": "hello"})
        self.assertEqual(workspace_bundle_text(ws, {"workspace_bundle": False}), "")
        self.assertEqual(workspace_bundle_text(ws, {"workspace_bundle": "off"}), "")

    def test_zero_cap_disables(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({"a.txt": "hello"})
        self.assertEqual(workspace_bundle_text(ws, {"workspace_bundle_max_chars": 0}), "")

    def test_provider_channel_skipped(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({"a.txt": "hello"})
        self.assertEqual(workspace_bundle_text(ws, {}, web_allowed=False), "")

    def test_self_upgrade_task_skipped(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({"a.txt": "hello"})
        self.assertEqual(workspace_bundle_text(ws, {}, self_task=True), "")

    def test_empty_workspace(self):
        from gantry.workspace_bundle import workspace_bundle_text

        self.assertEqual(workspace_bundle_text(_mk_ws(), {}), "")

    def test_truncation_note_visible(self):
        from gantry.workspace_bundle import workspace_bundle_text

        ws = _mk_ws({f"f{i}.txt": "c" for i in range(5)})
        text = workspace_bundle_text(ws, {"workspace_bundle_max_files": 2})
        self.assertIn("【注意】", text)
        self.assertIn("截断", text)


class FirstTurnWiringTests(unittest.TestCase):
    """接入点回归：打包只进首轮用户消息，不进系统提示词。"""

    def setUp(self):
        self.ws = _mk_ws({"现状说明.txt": "工作区里已有一份现状说明。"})

    def _runner(self, web_tools=True):
        from gantry.agent import AgentRunner
        from gantry.tools import ToolRegistry

        tools = ToolRegistry(self.ws, {"max_total_steps": 5})
        tools.set_web_copilot_tools(web_tools)
        r = AgentRunner(tools, lambda *a, **k: None, {"max_total_steps": 5},
                        state_getter=lambda: {"state": "ready"},
                        state_saver=lambda **k: None, step_saver=lambda s: None,
                        file_event_saver=lambda e: None)
        r._task_text = "分析这份现状"
        return r

    def test_bundle_in_first_prompt_only(self):
        from gantry.agent import AgentRunner
        from gantry.tools import ToolRegistry

        prompts = []
        state = {"state": "ready"}

        async def chat(prompt, model, make_new):
            prompts.append(prompt)
            if len(prompts) == 1:
                return json.dumps({"type": "tool", "tool": "list_directory",
                                   "arguments": {"path": "."}}, ensure_ascii=False)
            return json.dumps({"type": "final", "content": "done"}, ensure_ascii=False)

        tools = ToolRegistry(self.ws, {"max_total_steps": 5})
        tools.set_web_copilot_tools(True)
        r = AgentRunner(tools, chat, {"max_total_steps": 5},
                        state_getter=lambda: state,
                        state_saver=lambda **k: state.update(k),
                        step_saver=lambda s: None,
                        file_event_saver=lambda e: None)
        asyncio.run(r.run("分析这份现状", "m"))
        self.assertGreaterEqual(len(prompts), 2)
        self.assertIn("【工作区文件打包】", prompts[0])
        self.assertIn("现状说明.txt", prompts[0])
        self.assertNotIn("【工作区文件打包】", prompts[1], "只有首轮注入，避免每回合烧上下文")

    def test_bundle_not_in_system_prompt(self):
        r = self._runner()
        self.assertNotIn("【工作区文件打包】", r._system_prompt())
        self.assertEqual(r._system_prompt(), r._system_prompt())

    def test_provider_channel_no_bundle(self):
        from gantry.agent import AgentRunner
        from gantry.tools import ToolRegistry

        prompts = []
        state = {"state": "ready"}

        async def chat(prompt, model, make_new):
            prompts.append(prompt)
            return json.dumps({"type": "final", "content": "done"}, ensure_ascii=False)

        tools = ToolRegistry(self.ws, {"max_total_steps": 5})
        tools.set_web_copilot_tools(False)
        r = AgentRunner(tools, chat, {"max_total_steps": 5},
                        state_getter=lambda: state,
                        state_saver=lambda **k: state.update(k),
                        step_saver=lambda s: None,
                        file_event_saver=lambda e: None)
        asyncio.run(r.run("分析这份现状", "m"))
        self.assertTrue(prompts)
        self.assertNotIn("【工作区文件打包】", prompts[0], "provider 会话不注入打包")


if __name__ == "__main__":
    unittest.main()
