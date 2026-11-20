"""OpenAI Images Provider（gpt-image-1 / dall-e-3 等）。"""
from __future__ import annotations

import base64
import httpx

from .base import (GeneratedImage, GenerationResult, ImageGenerationError,
                   ImageProvider, ImageRequest, MAX_IMAGE_BYTES, validate_image_bytes)


class OpenAIProvider(ImageProvider):
    name = "openai"
    api_key_env = "OPENAI_API_KEY"
    quality_rank = 4
    cost_rank = 4

    def __init__(self, config=None):
        super().__init__(config)
        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = str(self.config.get("api_key") or "")
        self.model = self.model or "gpt-image-1"

    def available(self) -> bool:
        from .env import env
        return bool(self.api_key or env(self.api_key_env))

    def _key(self) -> str:
        from .env import env
        return self.api_key or env(self.api_key_env)

    async def generate(self, request: ImageRequest) -> GenerationResult:
        key = self._key()
        if not key:
            raise ImageGenerationError("OPENAI_API_KEY 未设置")
        if not self.supports(request):
            raise ImageGenerationError("OpenAI 一次最多生成 4 张图")
        body = {
            "model": request.model or self.model,
            "prompt": request.prompt,
            "size": request.size.replace("*", "x"),
            "n": max(1, min(4, int(request.n))),
        }
        if request.quality and str(request.quality).lower() in ("standard", "hd", "low", "medium", "high"):
            body["quality"] = request.quality
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                response = await client.post(self.base_url + "/images/generations",
                                             headers=headers, json=body)
                if response.status_code >= 400:
                    raise ImageGenerationError(
                        f"OpenAI 图片接口 HTTP {response.status_code}: {response.text[:300]}")
                items = (response.json().get("data") or [])
                images: list[GeneratedImage] = []
                for item in items[:4]:
                    data = b""
                    source = ""
                    if item.get("b64_json"):
                        data = base64.b64decode(item["b64_json"])
                    elif item.get("url"):
                        source = str(item["url"])
                        got = await client.get(source)
                        got.raise_for_status()
                        data = got.content
                    if not data:
                        continue
                    if len(data) > MAX_IMAGE_BYTES:
                        raise ImageGenerationError("OpenAI 图片超过 20 MB 限制")
                    mime, width, height = validate_image_bytes(data)
                    images.append(GeneratedImage(data=data, mime=mime, source_url=source,
                                                 width=width, height=height))
        except ImageGenerationError:
            raise
        except Exception as exc:
            raise ImageGenerationError(f"OpenAI 请求失败: {exc}") from exc
        if not images:
            raise ImageGenerationError("OpenAI 没有返回图片")
        return GenerationResult(images=images, provider=self.name,
                                model=request.model or self.model,
                                meta={"requested_size": request.size, "n": request.n})
