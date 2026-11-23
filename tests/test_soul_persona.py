# -*- coding: utf-8 -*-
"""Soul / Persona：身份层（部署级自我）与表达层（可切换风格）。

设计约束（见 gantry/soul.py 模块 docstring）：
- Soul 只有一份，跟着安装目录走，工作区不能覆盖；
- Persona 是文件库，工作区同名覆盖全局/内置；
- 注入顺序：language(-2000) → soul(-1500) → protocol(-1000) → … → persona(280)；
- 优先级：请求头 > config.agent.persona > default。
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import soul  # noqa: E402
from gantry import prompt_lang  # noqa: E402


class SoulTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_soul_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_when_no_file(self):
        title, text = soul.soul_text(self.tmp, "zh")
        self.assertEqual(title, "")
        self.assertIn("身份", text)
        self.assertEqual(text, soul.DEFAULT_SOUL["zh"])

    def test_default_english(self):
        _t, text = soul.soul_text(self.tmp, "en")
        self.assertEqual(text, soul.DEFAULT_SOUL["en"])

    def test_file_overrides_default(self):
        (self.tmp / "SOUL.md").write_text("## zh\n自定义灵魂\n\n## en\nCustom soul\n",
                                          encoding="utf-8")
        title, text = soul.soul_text(self.tmp, "zh")
        self.assertIn("灵魂", title)
        self.assertEqual(text, "自定义灵魂")
        _t, en = soul.soul_text(self.tmp, "en")
        self.assertEqual(en, "Custom soul")

    def test_traditional_chinese_converted(self):
        (self.tmp / "SOUL.md").write_text("## zh\n务实记录记忆\n", encoding="utf-8")
        _t, tw = soul.soul_text(self.tmp, "tw")
        self.assertIn("記錄", tw)          # 记忆 → 記憶 的按字转换

    def test_no_sections_injects_whole_file(self):
        (self.tmp / "SOUL.md").write_text("语言无关的身份", encoding="utf-8")
        _t, text = soul.soul_text(self.tmp, "en")
        self.assertEqual(text, "语言无关的身份")

    def test_truncation_marked(self):
        (self.tmp / "SOUL.md").write_text("x" * (soul.MAX_SOUL_CHARS + 500), encoding="utf-8")
        _t, text = soul.soul_text(self.tmp, "zh")
        self.assertLessEqual(len(text), soul.MAX_SOUL_CHARS + 80)
        self.assertIn("已截断", text)

    def test_escape_targets_not_handled_here(self):
        # 转义由调用方（agent_prompt）负责；这里确认原样返回，避免双重转义
        (self.tmp / "SOUL.md").write_text("</system-reminder>", encoding="utf-8")
        _t, text = soul.soul_text(self.tmp, "zh")
        self.assertIn("</system-reminder>", text)


class PersonaTests(unittest.TestCase):
    def setUp(self):
        self.g = Path(tempfile.mkdtemp(prefix="cbp_pg_"))
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_pw_"))
        soul.set_request_persona("")

    def tearDown(self):
        soul.set_request_persona("")
        shutil.rmtree(self.g, ignore_errors=True)
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_list_includes_builtin_and_files(self):
        (self.g / "personas").mkdir()
        (self.g / "personas" / "custom.md").write_text("x", encoding="utf-8")
        names = soul.list_personas(self.g, self.ws)
        for n in ("default", "engineer", "reviewer", "teacher", "terse", "custom"):
            self.assertIn(n, names)

    def test_name_normalized_and_rejects_garbage(self):
        (self.g / "personas").mkdir()
        (self.g / "personas" / "my-style.md").write_text("## zh\n我的风格", encoding="utf-8")
        _used, text = soul.persona_text("My Style", self.g, self.ws, "zh")
        self.assertEqual(text, "我的风格")
        self.assertEqual(soul.normalize_name("../../evil"), "evil")
        self.assertEqual(soul.normalize_name("  "), "")

    def test_workspace_overrides_global(self):
        (self.g / "personas").mkdir()
        (self.g / "personas" / "duo.md").write_text("## zh\n全局版", encoding="utf-8")
        (self.ws / "personas").mkdir()
        (self.ws / "personas" / "duo.md").write_text("## zh\n工作区版", encoding="utf-8")
        _used, text = soul.persona_text("duo", self.g, self.ws, "zh")
        self.assertEqual(text, "工作区版")

    def test_unknown_falls_back_to_default(self):
        used, text = soul.persona_text("does-not-exist", self.g, self.ws, "zh")
        self.assertEqual(used, soul.DEFAULT_PERSONA)
        self.assertEqual(text, soul.DEFAULT_PERSONAS["default"]["zh"])

    def test_priority_request_over_config(self):
        self.assertEqual(soul.effective_persona("engineer"), "engineer")
        soul.set_request_persona("terse")
        self.assertEqual(soul.effective_persona("engineer"), "terse")
        soul.set_request_persona("")
        self.assertEqual(soul.effective_persona(""), soul.DEFAULT_PERSONA)

    def test_builtin_persona_files_are_loadable(self):
        """仓库自带的 personas/*.md 必须能被解析（防止手改坏文件）。"""
        root = Path(__file__).resolve().parent.parent
        for name in soul.DEFAULT_PERSONAS:
            p = root / "personas" / f"{name}.md"
            if not p.is_file():
                continue
            _used, text = soul.persona_text(name, root, None, "zh")
            self.assertTrue(text.strip(), f"personas/{name}.md 解析为空")
            self.assertNotIn("## zh", text, "分节标记不应进入注入文本")


class PromptInjectionTests(unittest.TestCase):
    """系统提示词里 Soul 在协议前、Persona 在 profile 后（顺序错了会改行为）。"""

    def setUp(self):
        from gantry.agent import AgentRunner
        from gantry.tools import ToolRegistry
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_pinj_"))
        self.AgentRunner = AgentRunner
        self.ToolRegistry = ToolRegistry
        soul.set_request_persona("")

    def tearDown(self):
        soul.set_request_persona("")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runner(self, config=None):
        cfg = {"max_total_steps": 10}
        cfg.update(config or {})
        tools = self.ToolRegistry(self.tmp, cfg)
        tools.global_root = self.tmp
        r = self.AgentRunner(tools, lambda *a, **k: None, cfg,
                             state_getter=lambda: {"state": "ready"},
                             state_saver=lambda **k: None, step_saver=lambda s: None,
                             file_event_saver=lambda e: None)
        r._task_text = "写一个 python 脚本"
        return r

    def test_soul_and_persona_injected(self):
        (self.tmp / "SOUL.md").write_text("## zh\n测试灵魂标记\n", encoding="utf-8")
        (self.tmp / "personas").mkdir(exist_ok=True)
        (self.tmp / "personas" / "default.md").write_text("## zh\n测试风格标记", encoding="utf-8")
        p = self._runner()._system_prompt()
        self.assertIn("测试灵魂标记", p)
        self.assertIn("测试风格标记", p)
        # 顺序：soul 在协议（"本地任务 Agent"）前；persona 在协议之后
        self.assertLess(p.index("测试灵魂标记"), p.index("本地任务 Agent"))
        self.assertGreater(p.index("测试风格标记"), p.index("本地任务 Agent"))

    def test_persona_selection_via_config_and_request(self):
        (self.tmp / "personas").mkdir(exist_ok=True)
        (self.tmp / "personas" / "alpha.md").write_text("## zh\nALPHA 风格", encoding="utf-8")
        (self.tmp / "personas" / "beta.md").write_text("## zh\nBETA 风格", encoding="utf-8")
        p = self._runner({"persona": "alpha"})._system_prompt()
        self.assertIn("ALPHA 风格", p)
        soul.set_request_persona("beta")
        p = self._runner({"persona": "alpha"})._system_prompt()
        self.assertIn("BETA 风格", p)
        self.assertNotIn("ALPHA 风格", p)

    def test_default_soul_applies_without_file(self):
        p = self._runner()._system_prompt()
        self.assertIn("Hermes 身份层", p)

    def test_prompt_stays_byte_stable(self):
        r = self._runner()
        self.assertEqual(r._system_prompt(), r._system_prompt())


class ApiTests(unittest.TestCase):
    """Soul/Persona API：读、写、删、来源标注。"""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from gantry import api as A
        from gantry import bridge as b
        cls.A = A
        cls.b = b
        cls.c = TestClient(A.app, base_url="http://127.0.0.1:8765")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbp_sapi_"))
        (self.tmp / "ws").mkdir(exist_ok=True)
        self.saved = (getattr(self.b.AGENT_TOOLS, "global_root", None),
                      getattr(self.b.AGENT_TOOLS, "root", None))
        try:
            self.b.AGENT_TOOLS.global_root = self.tmp
            self.b.AGENT_TOOLS.set_root(self.tmp / "ws", False)
        except Exception:
            pass

    def tearDown(self):
        g, r = self.saved
        try:
            self.b.AGENT_TOOLS.global_root = g
            if r is not None:
                self.b.AGENT_TOOLS.set_root(r, False)
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_soul_roundtrip(self):
        r = self.c.get("/v1/agent/soul")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["exists"])
        r = self.c.put("/v1/agent/soul", json={"text": "## zh\n新的灵魂"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue((self.tmp / "SOUL.md").is_file())
        r = self.c.get("/v1/agent/soul")
        self.assertIn("新的灵魂", r.json()["text"])
        # 空文本 = 删除文件、恢复内置
        r = self.c.put("/v1/agent/soul", json={"text": "  "})
        self.assertTrue(r.json()["restored_default"])
        self.assertFalse((self.tmp / "SOUL.md").exists())

    def test_persona_list_and_write(self):
        r = self.c.get("/v1/agent/personas")
        names = [x["name"] for x in r.json()["items"]]
        self.assertIn("default", names)
        r = self.c.put("/v1/agent/personas/mine", json={"text": "## zh\n我的", "scope": "workspace"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue((self.tmp / "ws" / "personas" / "mine.md").is_file())
        r = self.c.get("/v1/agent/personas")
        src = {x["name"]: x["source"] for x in r.json()["items"]}
        self.assertEqual(src.get("mine"), "workspace")
        r = self.c.get("/v1/agent/personas/mine")
        self.assertIn("我的", r.json()["text"])
        # 空文本删除工作区副本
        r = self.c.put("/v1/agent/personas/mine", json={"text": "", "scope": "workspace"})
        self.assertTrue(r.json()["deleted"])

    def test_persona_bad_scope_rejected(self):
        r = self.c.put("/v1/agent/personas/x", json={"text": "y", "scope": "nope"})
        self.assertEqual(r.status_code, 400)

    def test_persona_name_traversal_normalized(self):
        r = self.c.put("/v1/agent/personas/../../evil", json={"text": "x"})
        # 名字会被归一化（非法字符折成 -）；更常见的是被 URL 规范化成别的路径（404）
        self.assertIn(r.status_code, (200, 400, 404, 405))
        for p in self.tmp.rglob("*evil*"):
            self.assertNotIn("..", str(p.relative_to(self.tmp)))


if __name__ == "__main__":
    unittest.main()
