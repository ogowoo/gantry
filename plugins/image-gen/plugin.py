"""image-gen 插件：统一 Provider 层的文生图工具。

Provider 实现位于 `providers/`：
- dashscope：通义万相异步任务；
- openai：Images API；
- router：auto 按能力/成本/质量策略选择，可回退。

旧工具名 `image_generate` 保持兼容；返回字段仍包含 `files`/`urls`，并新增
`provider`、`model`、`images` 元数据。
"""
from __future__ import annotations

import importlib
import sys
import time
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_providers = importlib.import_module("providers")
build_router = _providers.build_router
ImageRequest = _providers.ImageRequest


def _workspace(ctx) -> Path:
    try:
        from gantry import bridge as b
        ctx_var = getattr(b, "_TOOLS_CTX", None)
        reg = ctx_var.get() if ctx_var is not None else None
        if reg is None:
            reg = getattr(ctx.host, "registry", None)
        root = getattr(reg, "root", None) or getattr(ctx.host.registry, "root", None)
        return Path(root).resolve()
    except Exception:
        return Path(str(getattr(ctx.host.registry, "root", "."))).resolve()


def _save_image(ctx, name: str, data: bytes) -> str:
    root = _workspace(ctx)
    target = (root / name).resolve()
    if root not in target.parents:
        raise RuntimeError("生成图片路径越界")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target.relative_to(root).as_posix()


async def generate(ctx, prompt, size="1024*1024", n=1, backend="", model=""):
    prompt = str(prompt or "").strip()
    if not prompt:
        raise RuntimeError("prompt 不能为空")
    cfg = dict(ctx.config or {})
    router = build_router(cfg)
    req = ImageRequest(prompt=prompt,
                       size=str(size or cfg.get("size") or "1024*1024"),
                       n=max(1, min(4, int(n or 1))),
                       model=str(model or cfg.get("model") or ""))
    result = await router.generate(req, backend=str(backend or cfg.get("backend") or "auto"))
    stamp = time.strftime("%Y%m%d")
    files: list[str] = []
    images: list[dict] = []
    for i, image in enumerate(result.images[:4]):
        ext = {"image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(
            image.mime, "png")
        name = f"generated/{stamp}-{uuid.uuid4().hex[:8]}-{i}.{ext}"
        try:
            rel = _save_image(ctx, name, image.data)
        except Exception as exc:
            files.append(f"保存失败: {exc}")
            continue
        files.append(rel)
        images.append({"path": rel, "mime": image.mime, "width": image.width,
                       "height": image.height, "bytes": len(image.data),
                       "source_url": image.source_url})
    return {"backend": result.provider, "provider": result.provider,
            "model": result.model, "prompt": prompt, "size": size,
            "files": files, "images": images,
            "urls": [x.source_url for x in result.images if x.source_url]}


def setup(ctx):
    async def _tool(prompt: str, size: str = "", n: int = 1,
                    backend: str = "", model: str = ""):
        try:
            return await generate(ctx, prompt, size or ctx.config.get("size", "1024*1024"),
                                  n, backend=backend, model=model)
        except Exception as exc:
            return {"error": str(exc)[:600],
                    "backend": backend or ctx.config.get("backend", "auto"),
                    "providers": build_router(ctx.config).describe()}

    ctx.register_tool(
        "image_generate", _tool,
        "文生图：按提示词生成图片并保存到工作区 generated/（返回相对路径）",
        {"prompt": "画面描述", "size": "尺寸，默认 1024*1024", "n": "张数，默认 1",
         "backend": "auto|dashscope|openai", "model": "可选模型名"})

    async def _api(request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            return await generate(ctx, body.get("prompt") or "",
                                  body.get("size") or ctx.config.get("size", "1024*1024"),
                                  body.get("n") or 1,
                                  backend=body.get("backend") or "",
                                  model=body.get("model") or "")
        except Exception as exc:
            return {"error": str(exc)[:600],
                    "backend": body.get("backend") or ctx.config.get("backend", "auto"),
                    "providers": build_router(ctx.config).describe()}

    ctx.register_api("POST", "api/generate", _api)
    ctx.log("image-gen 已就绪（Provider: " +
            ", ".join(p.get("name", "?") for p in build_router(ctx.config).describe()) + "）")
