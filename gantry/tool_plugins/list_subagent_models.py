"""list_subagent_models tool plugin: discover routes usable by subagents."""
from __future__ import annotations

from typing import Any

from ..tools import ToolError


async def list_subagent_models(registry, provider: str = "", model: str = "") -> dict[str, Any]:
    """列出可用的提供商/模型（子代理可继承或显式指定）。"""
    from .. import bridge as b
    prov = str(provider or "").strip()
    mdl = str(model or "").strip()
    if prov and prov not in b.PROVIDERS:
        names = "、".join(sorted(b.PROVIDERS)) or "（无）"
        raise ToolError(f"没有提供商 {prov}。已配置：{names}")
    if prov:
        p = b.PROVIDERS[prov]
        models = list(p.models or [])
        out: dict[str, Any] = {"provider": prov, "configured": bool(p.configured),
                               "models": models}
        if mdl:
            out["model"] = mdl
            out["available"] = mdl in models
            if mdl not in models:
                out["hint"] = f"{prov} 的模型列表里没有 {mdl}，请确认模型名。"
        return out
    providers = [
        {"id": name, "configured": bool(p.configured),
         "description": p.description, "models": list(p.models or [])}
        for name, p in sorted(b.PROVIDERS.items())
    ]
    builtin = [
        {"id": mid, "description": item.get("description") or mid}
        for mid, item in sorted(b.MODELS.items())
    ]
    return {"providers": providers, "builtin_models": builtin,
            "note": "子代理不传 model 时跟随父会话；传 'provider/model' 可指定提供商模型。"}


def register(registry):
    async def _call(*args, **kwargs):
        return await list_subagent_models(registry, *args, **kwargs)
    registry.register(
        "list_subagent_models",
        _call,
        "列出子代理可用的模型路由（提供商/内置模型），需要给子代理指定 model 时先查一下",
        {"provider": "提供商 id（可选；不传则列全部）", "model": "具体模型 id（可选，用于校验是否可用）"},
    )
