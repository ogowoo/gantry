# -*- coding: utf-8 -*-
"""技能验证状态：未验证不冒充已验证。"""
import asyncio
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry.tools import ToolRegistry


class SkillVerifiedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill5_"))
        self.tools = ToolRegistry(self.tmp, {})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_verified_frontmatter_and_listing(self):
        r = asyncio.run(self.tools.execute("skill_create", {
            "name": "verified-flow", "content": "# Flow\n\nonly after validation",
            "description": "verified example", "verified": True}))
        self.assertTrue(r["verified"])
        skills = {s["name"]: s for s in self.tools.skill_list()}
        self.assertTrue(skills["verified-flow"]["verified"])

    def test_default_is_unverified(self):
        asyncio.run(self.tools.execute("skill_create", {
            "name": "draft-flow", "content": "# Draft\n\nnot validated yet"}))
        skills = {s["name"]: s for s in self.tools.skill_list()}
        self.assertFalse(skills["draft-flow"]["verified"])


if __name__ == "__main__":
    unittest.main()
