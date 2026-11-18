# -*- coding: utf-8 -*-
"""回归测试：自升级开关的接线（这次是真机发现的全链路问题）。

实测故障链：config.json 顶层 `allow_self_upgrade: true` → 但 `AGENT_TOOLS` 拿的是
`CFG["agent"]` 段 → 顶层键丢了 → 危险区开关拨了也没用（模型："未提供 self 工具"）。
更糟的是旧 config 的 agent 段里可能留着 `allow_self_upgrade: false` 残值，必须让顶层赢。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class SelfUpgradeWiringTests(unittest.TestCase):
    def test_key_lives_in_agent_section(self):
        """历史上这个键就在 config.json 的 agent 段（tools.py 从 AGENT_CFG 读，
        提示文案也写 agent.allow_self_upgrade）。schema 必须同名，不能造一个顶层键。"""
        src = (ROOT / "gantry" / "routes_config.py").read_text(encoding="utf-8")
        self.assertIn('"agent.allow_self_upgrade"', src, "schema 键必须在 agent 段")
        self.assertNotIn('"allow_self_upgrade": {"kind"', src, "不能再有顶层键")
        bsrc = (ROOT / "gantry" / "bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("for _top_key", bsrc, "不要把顶层键并进 agent 段（会漏进测试）")

    def test_live_apply_reaches_agent_tools(self):
        """危险区保存时直接写回 AGENT_TOOLS（不用重启），且走 agent.* 分支。"""
        src = (ROOT / "gantry" / "routes_config.py").read_text(encoding="utf-8")
        self.assertIn('key == "agent.allow_self_upgrade"', src)
        self.assertIn('b.AGENT_TOOLS.allow_self_upgrade = val', src)
        # 只许一个 agent. 分支（之前重复过）
        self.assertEqual(src.count('elif key.startswith("agent.")'), 1)

    def test_switch_on_actually_opens_tools(self):
        """开关打开时 self_* 工具必须进清单；关闭时不该出现。

        注意：**不能**断言 config.json 的当前值——那是用户随时会改的运行时状态
        （实测把它从 true 改回 false 就让这条测试红了）。这里自带配置、双向验证。
        """
        import shutil
        import tempfile

        from gantry.tools import ToolRegistry

        ws = Path(tempfile.mkdtemp(prefix="cbp_self_switch_"))
        try:
            off = ToolRegistry(ws, {"allow_self_upgrade": False})
            names_off = set(((off.tool_list().get("groups") or {}).get("self") or []))
            on = ToolRegistry(ws, {"allow_self_upgrade": True})
            names_on = set(((on.tool_list().get("groups") or {}).get("self") or []))
            self.assertTrue(on.allow_self_upgrade, "配置 true 应落到 allow_self_upgrade")
            self.assertFalse(off.allow_self_upgrade)
            self.assertIn("self_replace_text", names_on,
                          "开关打开后 self_* 工具必须在清单里（模型才能看到）")
            self.assertTrue(names_on)
            self.assertFalse(names_off, "开关关闭时不应出现 self_* 工具")
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
