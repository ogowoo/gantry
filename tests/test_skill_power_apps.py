# -*- coding: utf-8 -*-
"""power-apps 技能：可发现、内容包含 maker 门户与 UI 工具。"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.tools import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent


class PowerAppsSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gantry-papp-skill-"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        self.tools = ToolRegistry(self.ws, {})
        self.tools.set_global_root(REPO_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_discovered_and_readable(self):
        cat = {s["name"]: s for s in self.tools.skill_list()}
        self.assertIn("power-apps", cat)
        self.assertIn("Power Apps", cat["power-apps"]["description"])
        r = asyncio.run(self.tools.skill_read("power-apps"))
        self.assertEqual(r["name"], "power-apps")
        self.assertIn("make.powerapps.com", r["content"])
        self.assertIn("papp_snapshot", r["content"])


if __name__ == "__main__":
    unittest.main()
