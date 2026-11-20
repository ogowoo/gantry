"""文生图 Provider 的统一数据结构与接口。

image-gen 插件不再直接写死 DashScope / OpenAI 请求；Provider 只描述
“一次生成需要什么、返回什么”，router 负责能力路由与回退。
"""
from __future__ import annotations

import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 4


class ImageGenerationError(RuntimeError):
    """可预期的生成失败；消息可以直接反馈给 Agent。"""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    size: str = "1024*1024"
    n: int = 1
    model: str = ""
    quality: str = "standard"
    negative_prompt: str = ""
    output_format: str = "png"


@dataclass
class GeneratedImage:
    data: bytes
    mime: str = "image/png"
    source_url: str = ""
    width: int = 0
    height: int = 0


@dataclass
class GenerationResult:
    images: list[GeneratedImage]
    provider: str
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def validate_image_bytes(data: bytes, *, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[str, int, int]:
    """限制大小并验证确实是浏览器/Pillow 可读的图片。"""
    if not data:
        raise ImageGenerationError("Provider 返回空图片")
    if len(data) > max_bytes:
        raise ImageGenerationError(f"图片超过 {max_bytes // (1024 * 1024)} MB 限制")
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                    "GIF": "image/gif"}.get(str(im.format).upper(), "image/png")
            return mime, int(im.width), int(im.height)
    except Exception as exc:
        raise ImageGenerationError(f"Provider 返回的图片无法解码: {exc}") from exc


class ImageProvider(ABC):
    name = ""
    api_key_env = ""
    quality_rank = 0       # 越大越偏质量
    cost_rank = 0          # 越大越贵
    supports_edit = False

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.model = str(self.config.get("model") or "")

    @abstractmethod
    def available(self) -> bool:
        """API key / endpoint 是否满足调用条件。"""

    @abstractmethod
    async def generate(self, request: ImageRequest) -> GenerationResult:
        """执行一次文生图。"""

    def supports(self, request: ImageRequest) -> bool:
        return 1 <= request.n <= MAX_IMAGES

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "model": self.model or "default",
                "quality_rank": self.quality_rank, "cost_rank": self.cost_rank,
                "available": self.available(), "supports_edit": self.supports_edit}
