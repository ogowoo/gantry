"""文生图能力路由：显式 backend 优先，auto 按策略排序，可回退。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .base import GenerationResult, ImageGenerationError, ImageProvider, ImageRequest


@dataclass
class ProviderRouter:
    providers: list[ImageProvider]
    strategy: str = "balanced"
    allow_fallback: bool = True

    def __post_init__(self) -> None:
        self.strategy = str(self.strategy or "balanced").lower()
        if self.strategy not in ("balanced", "quality", "cost"):
            self.strategy = "balanced"

    def _key(self, provider: ImageProvider) -> tuple:
        if self.strategy == "quality":
            return (-provider.quality_rank, provider.cost_rank, provider.name)
        if self.strategy == "cost":
            return (provider.cost_rank, -provider.quality_rank, provider.name)
        return (provider.cost_rank - provider.quality_rank,
                -provider.quality_rank, provider.name)

    def candidates(self, request: ImageRequest, backend: str = "auto") -> list[ImageProvider]:
        backend = str(backend or "auto").lower()
        if backend != "auto":
            chosen = [p for p in self.providers if p.name == backend]
            if not chosen:
                raise ImageGenerationError(f"未知图片后端: {backend}")
            if not chosen[0].available():
                raise ImageGenerationError(f"图片后端 {backend} 未配置 API Key")
            if not chosen[0].supports(request):
                raise ImageGenerationError(f"图片后端 {backend} 不支持本次参数")
            return chosen
        out = [p for p in self.providers if p.available() and p.supports(request)]
        out.sort(key=self._key)
        if not out:
            raise ImageGenerationError(
                "没有可用的文生图后端：请配置 DASHSCOPE_API_KEY 或 OPENAI_API_KEY")
        return out

    async def generate(self, request: ImageRequest, backend: str = "auto") -> GenerationResult:
        if not str(request.prompt or "").strip():
            raise ImageGenerationError("prompt 不能为空")
        candidates = self.candidates(request, backend)
        errors: list[str] = []
        for provider in (candidates if (self.allow_fallback or str(backend).lower() == "auto")
                         else candidates[:1]):
            try:
                return await provider.generate(request)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
                if str(backend).lower() != "auto" and not self.allow_fallback:
                    break
        raise ImageGenerationError("所有图片后端都失败: " + " | ".join(errors)[:800])

    def describe(self) -> list[dict]:
        return [p.describe() for p in self.providers]
