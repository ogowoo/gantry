# -*- coding: utf-8 -*-
"""image_generate 兼容工具 + Provider 路由的最小集成测试（无网络）。"""
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from gantry.plugins import PluginHost
from gantry.tools import ToolRegistry


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (24, 18), (255, 120, 0)).save(buf, format="PNG")
    return buf.getvalue()


class FakeRouter:
    def __init__(self):
        self.calls = []

    def describe(self):
        return [{"name": "fake", "model": "fake-model", "available": True}]

    async def generate(self, req, backend="auto"):
        self.calls.append((req, backend))
        from providers.base import GeneratedImage, GenerationResult
        return GenerationResult(images=[GeneratedImage(_png(), "image/png", width=24, height=18)],
                                provider="fake", model="fake-model")


class ImageGenCompatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="img_gen_test_"))
        self.ws = self.tmp / "workspace"
        self.ws.mkdir()
        self.registry = ToolRegistry(self.ws, {})
        self.host = PluginHost(ROOT, registry=self.registry)
        self.host.load("image-gen")
        self.mod = self.host.records["image-gen"]["module"]
        self.fake = FakeRouter()
        self.old_build = self.mod.build_router
        self.mod.build_router = lambda config: self.fake

    def tearDown(self):
        self.mod.build_router = self.old_build
        self.host.unload("image-gen")
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def test_image_generate_still_works_and_saves_workspace_file(self):
        out = await self.registry.execute("image_generate", {
            "prompt": "橙色海报背景", "size": "512*512", "n": 1,
            "backend": "fake", "model": "fake-model"})
        self.assertEqual(out["provider"], "fake")
        self.assertEqual(len(out["files"]), 1)
        self.assertTrue((self.ws / out["files"][0]).is_file())
        self.assertEqual(out["images"][0]["width"], 24)
        self.assertEqual(self.fake.calls[0][1], "fake")


if __name__ == "__main__":
    unittest.main()
