# -*- coding: utf-8 -*-
"""visual-design 插件：HTML/SVG 渲染、尺寸、中文、路径、超时、恢复、安装/卸载。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.plugins import PluginHost
from gantry.tools import ToolRegistry

PLUGIN = ROOT / "plugins" / "visual-design"


class VisualDesignPluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vd_test_"))
        self.ws = self.tmp / "workspace"
        self.ws.mkdir()
        self.registry = ToolRegistry(self.ws, {})
        self.host = PluginHost(ROOT, registry=self.registry)
        self.host.load("visual-design")

    def tearDown(self):
        self.host.unload("visual-design")
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def _project(self, title="测试海报", width=640, height=360):
        return await self.registry.execute("design_create", {
            "title": title, "width": width, "height": height,
            "brief": "测试",
        })

    async def test_canvas_and_asset_import(self):
        from PIL import Image
        p = await self._project("资产测试", 320, 240)
        root = self.ws / p["project"]
        self.assertTrue((root / "canvas.json").is_file())
        self.assertTrue((root / "assets" / "index.json").is_file())
        gen = self.ws / "generated"
        gen.mkdir()
        img = gen / "scene.png"
        Image.new("RGB", (64, 48), (20, 120, 200)).save(img)
        r = await self.registry.execute("asset_import", {
            "project_id": p["project_id"], "source": "generated/scene.png",
            "role": "background"})
        self.assertTrue(r["ok"])
        self.assertTrue((root / r["path"]).is_file())
        index = json.loads((root / "assets" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["assets"][0]["width"], 64)
        self.assertEqual(index["assets"][0]["height"], 48)
        canvas = json.loads((root / "canvas.json").read_text(encoding="utf-8"))
        self.assertIn(r["asset_id"], canvas["assets"])
        inspect = await self.registry.execute("design_inspect", {"project_id": p["project_id"]})
        self.assertTrue(inspect["canvas"])
        self.assertEqual(len(inspect["assets"]), 1)
        with self.assertRaises(Exception):
            await self.registry.execute("asset_import", {
                "project_id": p["project_id"], "source": "../outside.png"})

    async def test_html_render_1920x1080(self):
        p = await self._project("中文宣传海报", 1920, 1080)
        r = await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "html"})
        self.assertEqual((r["width"], r["height"]), (1920, 1080))
        self.assertTrue((self.ws / p["project"] / r["png"]).is_file())

    async def test_svg_render(self):
        p = await self._project("SVG 海报", 800, 600)
        r = await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "svg"})
        self.assertEqual((r["width"], r["height"]), (800, 600))
        self.assertTrue(r["png"].endswith("render-svg.png"))

    async def test_chinese_text_not_blank(self):
        p = await self._project("六一儿童节", 640, 360)
        r = await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "html"})
        from PIL import Image
        with Image.open(self.ws / p["project"] / r["png"]) as im:
            # 画面不是纯白：标题至少产生了非背景像素
            colors = im.convert("RGB").getcolors(maxcolors=1000000)
            self.assertTrue(colors and len(colors) > 1)

    async def test_inspect_and_export(self):
        p = await self._project("导出测试", 320, 240)
        await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "html"})
        preview = await self.registry.execute("design_preview", {
            "project_id": p["project_id"], "target": "html", "max_side": 160})
        self.assertLessEqual(max(preview["width"], preview["height"]), 160)
        i = await self.registry.execute("design_inspect", {"project_id": p["project_id"]})
        self.assertTrue(i["ok"], i["issues"])
        png = await self.registry.execute("design_export", {
            "project_id": p["project_id"], "format": "png"})
        jpg = await self.registry.execute("design_export", {
            "project_id": p["project_id"], "format": "jpg", "filename": "design.jpg"})
        svg = await self.registry.execute("design_export", {
            "project_id": p["project_id"], "format": "svg"})
        self.assertTrue(png["path"].endswith("design.png"))
        self.assertTrue(jpg["path"].endswith("design.jpg"))
        self.assertTrue(svg["path"].endswith("design.svg"))

    async def test_six_tools_registered(self):
        names = {"design_create", "asset_import", "design_modify", "design_render",
                 "design_preview", "design_inspect", "design_export"}
        self.assertTrue(names.issubset(set(self.registry._tools)))

    async def test_symlink_source_escape_rejected(self):
        p = await self._project()
        source = self.ws / p["project"] / "source" / "index.html"
        outside = self.tmp / "outside.html"
        outside.write_text("<h1>outside</h1>", encoding="utf-8")
        source.unlink()
        try:
            source.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")
        with self.assertRaises(Exception) as cm:
            await self.registry.execute("design_render", {
                "project_id": p["project_id"], "target": "html"})
        self.assertEqual(getattr(cm.exception, "code", ""), "PATH_ESCAPE")

    async def test_missing_source_and_invalid_input(self):
        p = await self._project()
        source = self.ws / p["project"] / "source" / "index.html"
        source.unlink()
        with self.assertRaises(Exception):
            await self.registry.execute("design_render", {
                "project_id": p["project_id"], "target": "html"})
        with self.assertRaises(Exception):
            await self.registry.execute("design_create", {
                "project_id": "../escape", "title": "x"})
        with self.assertRaises(Exception):
            await self.registry.execute("design_create", {
                "title": "x", "width": 10, "height": 10})
        with self.assertRaises(Exception):
            await self.registry.execute("design_export", {
                "project_id": p["project_id"], "format": "pdf"})
        with self.assertRaises(Exception):
            await self.registry.execute("design_render", {
                "project_id": p["project_id"], "output": "../x.png"})

    async def test_render_timeout_and_old_output_preserved(self):
        p = await self._project()
        await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "html"})
        out = self.ws / p["project"] / "renders" / "render-html.png"
        old = out.read_bytes()
        spec_path = self.ws / p["project"] / "design-spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["render"]["timeout_seconds"] = 0.01
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(Exception) as cm:
            await self.registry.execute("design_render", {
                "project_id": p["project_id"], "target": "html"})
        self.assertIn(getattr(cm.exception, "code", ""), ("RENDER_TIMEOUT", "RENDER_FAILED"))
        self.assertEqual(out.read_bytes(), old, "失败不能覆盖旧输出")

    async def test_spec_size_mismatch_reported(self):
        p = await self._project("尺寸检查", 320, 240)
        await self.registry.execute("design_render", {
            "project_id": p["project_id"], "target": "html"})
        spec_path = self.ws / p["project"] / "design-spec.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["canvas"]["width"] = 640
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        i = await self.registry.execute("design_inspect", {"project_id": p["project_id"]})
        self.assertFalse(i["ok"])
        self.assertTrue(any("尺寸" in x for x in i["issues"]))


class VisualDesignInstallTests(unittest.TestCase):
    def test_install_zip_and_uninstall(self):
        root = Path(tempfile.mkdtemp(prefix="vd_zip_"))
        try:
            zip_path = root / "visual-design.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in PLUGIN.rglob("*"):
                    if p.is_file():
                        zf.write(p, p.relative_to(PLUGIN).as_posix())
            ws = root / "workspace"
            ws.mkdir()
            host = PluginHost(root, registry=ToolRegistry(ws, {}))
            r = host.install(str(zip_path), force=True)
            self.assertEqual(r["name"], "visual-design")
            self.assertTrue(host.describe("visual-design")["removable"])
            host.uninstall("visual-design", keep_backup=False)
            self.assertFalse((root / "plugins" / "visual-design").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_install_and_uninstall_builtin_allow_uninstall(self):
        root = Path(tempfile.mkdtemp(prefix="vd_install_"))
        try:
            plugins = root / "plugins"
            shutil.copytree(PLUGIN, plugins / "visual-design")
            ws = root / "workspace"
            ws.mkdir()
            reg = ToolRegistry(ws, {})
            host = PluginHost(root, registry=reg)
            host.load("visual-design")
            self.assertTrue(host.describe("visual-design")["removable"])
            host.uninstall("visual-design", keep_backup=False)
            self.assertFalse((plugins / "visual-design").exists())
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
