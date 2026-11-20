"""通义万相 Provider（DashScope 异步任务：创建 → 轮询 → 下载）。"""
from __future__ import annotations

import asyncio
import time

import httpx

from .base import (GeneratedImage, GenerationResult, ImageGenerationError,
                   ImageProvider, ImageRequest, MAX_IMAGE_BYTES, validate_image_bytes)

_CREATE = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
_TASK = "https://dashscope.aliyuncs.com/api/v1/tasks/{}"


class DashScopeProvider(ImageProvider):
    name = "dashscope"
    api_key_env = "DASHSCOPE_API_KEY"
    quality_rank = 3
    cost_rank = 2

    def __init__(self, config=None):
        super().__init__(config)
        self.api_key = str(self.config.get("api_key") or "")
        self.model = self.model or "wanx2.1-t2i-turbo"
        self.timeout = float(self.config.get("timeout_seconds") or 180)

    def available(self) -> bool:
        from .env import env
        return bool(self.api_key or env(self.api_key_env))

    def _key(self) -> str:
        from .env import env
        return self.api_key or env(self.api_key_env)

    async def generate(self, request: ImageRequest) -> GenerationResult:
        key = self._key()
        if not key:
            raise ImageGenerationError("DASHSCOPE_API_KEY 未设置")
        if not self.supports(request):
            raise ImageGenerationError("DashScope 一次最多生成 4 张图")
        headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json",
                   "X-DashScope-Async": "enable"}
        body = {"model": request.model or self.model,
                "input": {"prompt": request.prompt},
                "parameters": {"size": request.size.replace("x", "*"),
                               "n": max(1, min(4, int(request.n)))}}
        images: list[GeneratedImage] = []
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
                r = await client.post(_CREATE, headers=headers, json=body)
                if r.status_code >= 400:
                    raise ImageGenerationError(
                        f"DashScope 创建任务 HTTP {r.status_code}: {r.text[:300]}")
                data = r.json()
                task_id = ((data.get("output") or {}).get("task_id"))
                if not task_id:
                    raise ImageGenerationError(f"DashScope 创建任务失败: {str(data)[:300]}")
                deadline = time.time() + self.timeout
                urls: list[str] = []
                while time.time() < deadline:
                    await asyncio.sleep(3)
                    t = await client.get(_TASK.format(task_id), headers=headers)
                    if t.status_code >= 400:
                        raise ImageGenerationError(
                            f"DashScope 查询任务 HTTP {t.status_code}: {t.text[:300]}")
                    out = t.json().get("output") or {}
                    status = out.get("task_status")
                    if status == "SUCCEEDED":
                        urls = [str(x.get("url")) for x in (out.get("results") or [])
                                if x.get("url")]
                        break
                    if status in ("FAILED", "CANCELED"):
                        raise ImageGenerationError(f"DashScope 任务失败: {str(out)[:300]}")
                if not urls:
                    raise ImageGenerationError(f"DashScope 生成超时（>{self.timeout:g}s）")
                for url in urls[:4]:
                    got = await client.get(url)
                    got.raise_for_status()
                    data_bytes = got.content
                    if len(data_bytes) > MAX_IMAGE_BYTES:
                        raise ImageGenerationError("DashScope 图片超过 20 MB 限制")
                    mime, width, height = validate_image_bytes(data_bytes)
                    images.append(GeneratedImage(data=data_bytes, mime=mime,
                                                 source_url=url, width=width, height=height))
        except ImageGenerationError:
            raise
        except Exception as exc:
            raise ImageGenerationError(f"DashScope 请求失败: {exc}") from exc
        if not images:
            raise ImageGenerationError("DashScope 没有返回图片")
        return GenerationResult(images=images, provider=self.name,
                                model=request.model or self.model,
                                meta={"requested_size": request.size, "n": request.n})
