"""图片 Provider 工厂。"""
from __future__ import annotations

from typing import Any

from .base import (GeneratedImage, GenerationResult, ImageGenerationError,
                   ImageProvider, ImageRequest, MAX_IMAGE_BYTES, validate_image_bytes)
from .dashscope import DashScopeProvider
from .openai import OpenAIProvider
from .router import ProviderRouter


def build_router(config: dict[str, Any] | None = None) -> ProviderRouter:
    cfg = dict(config or {})
    model = str(cfg.get("model") or "")
    providers: list[ImageProvider] = []
    if str(cfg.get("enable_dashscope", True)).lower() not in ("0", "false", "no"):
        providers.append(DashScopeProvider({"model": model,
                                            **dict(cfg.get("dashscope") or {})}))
    if str(cfg.get("enable_openai", True)).lower() not in ("0", "false", "no"):
        providers.append(OpenAIProvider({"model": model,
                                         **dict(cfg.get("openai") or {})}))
    return ProviderRouter(
        providers=providers,
        strategy=str(cfg.get("auto_strategy") or "balanced"),
        allow_fallback=bool(cfg.get("allow_fallback", True)),
    )


__all__ = [
    "GeneratedImage", "GenerationResult", "ImageGenerationError", "ImageProvider",
    "ImageRequest", "MAX_IMAGE_BYTES", "validate_image_bytes", "ProviderRouter",
    "DashScopeProvider", "OpenAIProvider", "build_router",
]
