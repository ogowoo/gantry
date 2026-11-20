# -*- coding: utf-8 -*-
"""image-gen Provider 层：路由、回退、图片校验。"""
import io
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PKG = ROOT / "plugins" / "image-gen" / "providers"
spec = importlib.util.spec_from_file_location(
    "image_gen_providers", PKG / "__init__.py", submodule_search_locations=[str(PKG)])
providers = importlib.util.module_from_spec(spec)
sys.modules["image_gen_providers"] = providers
spec.loader.exec_module(providers)


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), (0, 128, 255)).save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider(providers.ImageProvider):
    def __init__(self, name, available=True, quality=1, cost=1, fail=False):
        super().__init__({})
        self.name = name
        self.quality_rank = quality
        self.cost_rank = cost
        self._available = available
        self.fail = fail

    def available(self):
        return self._available

    async def generate(self, request):
        if self.fail:
            raise providers.ImageGenerationError(self.name + " failed")
        return providers.GenerationResult(
            images=[providers.GeneratedImage(_png(), "image/png", width=16, height=12)],
            provider=self.name, model="fake")


class ImageProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_image_bytes_validation(self):
        mime, w, h = providers.validate_image_bytes(_png())
        self.assertEqual((mime, w, h), ("image/png", 16, 12))
        with self.assertRaises(providers.ImageGenerationError):
            providers.validate_image_bytes(b"not an image")

    async def test_quality_and_cost_routing(self):
        a = FakeProvider("cheap", quality=1, cost=1)
        b = FakeProvider("quality", quality=5, cost=5)
        req = providers.ImageRequest("poster")
        self.assertEqual(ProviderRouterFor(a, b, "quality").candidates(req)[0].name, "quality")
        self.assertEqual(ProviderRouterFor(a, b, "cost").candidates(req)[0].name, "cheap")

    async def test_auto_falls_back(self):
        bad = FakeProvider("bad", fail=True)
        good = FakeProvider("good")
        router = providers.ProviderRouter([bad, good], strategy="balanced", allow_fallback=True)
        result = await router.generate(providers.ImageRequest("poster"))
        self.assertEqual(result.provider, "good")

    async def test_explicit_backend_unavailable(self):
        router = providers.ProviderRouter([FakeProvider("off", available=False)])
        with self.assertRaises(providers.ImageGenerationError):
            await router.generate(providers.ImageRequest("poster"), backend="off")


def ProviderRouterFor(a, b, strategy):
    return providers.ProviderRouter([a, b], strategy=strategy)


if __name__ == "__main__":
    unittest.main()
