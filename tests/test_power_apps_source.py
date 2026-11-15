# -*- coding: utf-8 -*-
"""Power Apps Canvas App YAML source 生成器：结构、校验与 pac 打包失败提示。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml

from gantry import power_apps_source as pas


class PowerAppsSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="papp_src_"))
        self.spec = {
            "display_name": "Gantry E2E App",
            "screen_name": "Home",
            "controls": [
                {"name": "lblTitle", "type": "Label", "text": "Hello Gantry",
                 "x": 40, "y": 40, "width": 600, "height": 80, "size": 28},
                {"name": "btnGo", "type": "Button", "text": "Go",
                 "x": 40, "y": 140, "width": 160, "height": 60},
            ],
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generate_and_validate(self):
        out = pas.generate_source("demo-app", self.spec, self.tmp / "demo-app")
        self.assertTrue((Path(out["dir"]) / "CanvasManifest.json").is_file())
        self.assertTrue((Path(out["dir"]) / "Src" / "App.pa.yaml").is_file())
        self.assertTrue((Path(out["dir"]) / "Src" / "Home.pa.yaml").is_file())
        app = yaml.safe_load((Path(out["dir"]) / "Src" / "App.pa.yaml").read_text(
            encoding="utf-8"))
        self.assertEqual(app["App"]["Properties"]["StartScreen"], "=Home")
        screen = yaml.safe_load((Path(out["dir"]) / "Src" / "Home.pa.yaml").read_text(
            encoding="utf-8"))
        self.assertIn("Home", screen["Screens"])
        self.assertEqual(len(screen["Screens"]["Home"]["Children"]), 2)
        manifest = json.loads((Path(out["dir"]) / "CanvasManifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["FormatVersion"], "0.24")
        self.assertTrue(pas.validate_source(Path(out["dir"]))["ok"])

    def test_pack_without_pac_has_install_hint(self):
        out = pas.generate_source("demo-app", self.spec, self.tmp / "demo-app")
        old = shutil.which
        shutil.which = lambda name: None
        try:
            with self.assertRaises(pas.PowerAppsSourceError) as ctx:
                pas.pack_msapp(out["dir"])
        finally:
            shutil.which = old
        self.assertIn("dotnet tool install", str(ctx.exception))

    def test_pack_command_and_output(self):
        out = pas.generate_source("demo-app", self.spec, self.tmp / "demo-app")
        target = self.tmp / "demo.msapp"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            target.write_bytes(b"MSAPP")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        old_run, old_which = pas.subprocess.run, shutil.which
        pas.subprocess.run = fake_run
        shutil.which = lambda name: "pac.exe"
        try:
            result = pas.pack_msapp(out["dir"], target)
        finally:
            pas.subprocess.run, shutil.which = old_run, old_which
        self.assertTrue(result["ok"])
        self.assertEqual(result["msapp"], str(target))
        self.assertIn("canvas", calls[0])
        self.assertIn("pack", calls[0])
        self.assertIn("SourceCode", calls[0])


if __name__ == "__main__":
    unittest.main()
