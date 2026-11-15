# -*- coding: utf-8 -*-
"""Power Apps Canvas App 源码生成工具：生成 YAML source，可选 pac 打包 .msapp。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import power_apps_source as pas
from ..tools import ToolError


def _spec(spec: Any) -> dict[str, Any]:
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception as exc:                 # noqa: BLE001
            raise ToolError(f"spec 不是有效 JSON: {exc}")
    if not isinstance(spec, dict):
        raise ToolError("spec 必须是对象或 JSON 字符串")
    return spec


async def power_apps_generate(registry, name: str, spec: Any,
                              output_dir: str = "apps", pack: bool = False,
                              pac: str = "", msapp: str = "") -> dict[str, Any]:
    """生成 Canvas App unpacked source；pack=True 时再调用 pac canvas pack。"""
    payload = _spec(spec)
    payload.setdefault("display_name", name)
    out = registry._path(str(output_dir or "apps"))
    out.mkdir(parents=True, exist_ok=True)
    app_dir = out / pas._slug(name)
    try:
        result = pas.generate_source(name, payload, app_dir)
        report = pas.validate_source(app_dir)
        result["validation"] = report
        if pack:
            msapp_path = registry._path(msapp) if msapp else (out / f"{result['name']}.msapp")
            result["msapp"] = pas.pack_msapp(app_dir, msapp_path, pac=pac)
        return result
    except pas.PowerAppsSourceError as exc:
        raise ToolError(str(exc))


async def power_apps_pack(registry, source_dir: str, msapp: str = "",
                          pac: str = "") -> dict[str, Any]:
    """把已有 Canvas App source 目录打包成 .msapp。"""
    src = registry._path(str(source_dir or ""))
    target = registry._path(str(msapp or "")) if msapp else src.with_suffix(".msapp")
    try:
        return pas.pack_msapp(src, target, pac=pac)
    except pas.PowerAppsSourceError as exc:
        raise ToolError(str(exc))


async def power_apps_validate(registry, source_dir: str) -> dict[str, Any]:
    """校验 Canvas App source 目录。"""
    try:
        return pas.validate_source(registry._path(str(source_dir or "")))
    except pas.PowerAppsSourceError as exc:
        raise ToolError(str(exc))


def register(registry):
    async def _generate(*args, **kwargs):
        return await power_apps_generate(registry, *args, **kwargs)

    async def _pack(*args, **kwargs):
        return await power_apps_pack(registry, *args, **kwargs)

    async def _validate(*args, **kwargs):
        return await power_apps_validate(registry, *args, **kwargs)

    registry.register(
        "power_apps_generate", _generate,
        "生成 Power Apps Canvas App 的 YAML source（CanvasManifest.json + Src/*.pa.yaml）；可选 pac 打包 .msapp",
        {"name": "应用名（小写短横线）", "spec": "对象或 JSON：display_name/screen_name/controls",
         "output_dir": "输出目录，默认 apps", "pack": "是否调用 pac canvas pack",
         "pac": "pac 可执行文件路径（可选）", "msapp": "输出 .msapp 路径（可选）"})
    registry.register(
        "power_apps_pack", _pack,
        "用 pac canvas pack 把 source 目录打包成 .msapp（需要安装 pac）",
        {"source_dir": "source 目录", "msapp": "输出 .msapp（可选）", "pac": "pac 路径（可选）"})
    registry.register(
        "power_apps_validate", _validate,
        "校验 Canvas App source：manifest、App.pa.yaml、Screen 文件",
        {"source_dir": "source 目录"})
