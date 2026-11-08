"""Power Automate 工程生成工具（同租户、不调用真实 API）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import power_automate as pa
from ..tools import ToolError


async def power_automate_catalog(registry, connector: str = "") -> dict[str, Any]:
    """列出内置连接器与操作（给生成器选型用）。"""
    key = str(connector or "").strip().lower()
    if key:
        if key not in pa.CONNECTORS:
            names = "、".join(sorted(pa.CONNECTORS))
            raise ToolError(f"没有连接器 {connector}。可用：{names}")
        c = pa.CONNECTORS[key]
        return {"connector": key, "display_name": c["display_name"],
                "ref": c["ref"], "api_id": c["api_id"],
                "operations": [{"key": op, "operation_id": v["operation_id"],
                                "summary": v["summary"],
                                "parameters": list((v.get("parameters") or {}).keys())}
                               for op, v in c["operations"].items()]}
    return {
        "connectors": [
            {"key": k, "display_name": v["display_name"], "ref": v["ref"],
             "api_id": v["api_id"],
             "operations": [{"key": op, "operation_id": o["operation_id"],
                             "summary": o["summary"]}
                            for op, o in v["operations"].items()]}
            for k, v in sorted(pa.CONNECTORS.items())
        ],
        "control_actions": [{"key": k, "summary": v} for k, v in sorted(pa.CONTROL_ACTIONS.items())],
    }


def _parse_spec(spec: Any) -> dict[str, Any]:
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception as exc:
            raise ToolError(f"spec 不是有效 JSON: {exc}")
    if not isinstance(spec, dict):
        raise ToolError("spec 必须是对象（或 JSON 字符串）")
    return spec


def _snapper(registry):
    def snap(rel: str, absolute: Any) -> None:
        registry._snap("power_automate_generate", rel, Path(absolute))
    return snap


async def power_automate_generate(registry, name: str, spec: Any,
                                  target: str = "all",
                                  output_dir: str = "flows",
                                  publisher: str = "GantryPublisher",
                                  prefix: str = "new",
                                  version: str = "1.0.0.0") -> dict[str, Any]:
    """按 spec 生成 Power Automate 工程与可导入包。"""
    payload = _parse_spec(spec)
    payload.setdefault("name", name)
    payload.setdefault("display_name", name)
    target = str(target or "all").strip().lower()
    if target not in ("project", "legacy", "solution", "all"):
        raise ToolError("target 只能是 project / legacy / solution / all")

    root = Path(registry.root)
    out_dir = str(output_dir or "flows").strip() or "flows"
    out_path = registry._path(out_dir)  # 沙箱校验
    out_path.mkdir(parents=True, exist_ok=True)

    snap = _snapper(registry)
    try:
        project = pa.write_flow_project(root, payload, snap=snap, output_dir=out_dir)
    except pa.PowerAutomateError as exc:
        raise ToolError(str(exc))

    result: dict[str, Any] = {"name": project["name"], "project_dir": project["dir"],
                              "files": project["files"], "packages": {}}
    if target in ("legacy", "all"):
        try:
            pkg = pa.build_legacy_package(root, payload, snap=snap, output_dir=out_dir)
        except pa.PowerAutomateError as exc:
            raise ToolError(str(exc))
        result["packages"]["legacy"] = str(pkg)
    if target in ("solution", "all"):
        try:
            pkg = pa.build_solution_package(root, payload, publisher=publisher,
                                            prefix=prefix, version=version,
                                            snap=snap, output_dir=out_dir)
        except pa.PowerAutomateError as exc:
            raise ToolError(str(exc))
        result["packages"]["solution"] = str(pkg)
    try:
        result["validation"] = pa.validate_project(root, project["name"],
                                                   output_dir=out_dir)
    except pa.PowerAutomateError as exc:
        result["validation"] = {"ok": False, "problems": [str(exc)]}
    return result


async def power_automate_validate(registry, name: str, output_dir: str = "flows") -> dict[str, Any]:
    """校验已生成的工程目录（JSON 可解析 + definition 必填键）。"""
    try:
        return pa.validate_project(Path(registry.root), name, output_dir=output_dir)
    except pa.PowerAutomateError as exc:
        raise ToolError(str(exc))


def register(registry):
    async def _catalog(*args, **kwargs):
        return await power_automate_catalog(registry, *args, **kwargs)

    async def _generate(*args, **kwargs):
        return await power_automate_generate(registry, *args, **kwargs)

    async def _validate(*args, **kwargs):
        return await power_automate_validate(registry, *args, **kwargs)

    registry.register(
        "power_automate_catalog",
        _catalog,
        "列出 Power Automate 内置连接器/操作与控制动作（同租户），生成前先查可用项",
        {"connector": "连接器 key（可选；不传则列全部）"},
    )
    registry.register(
        "power_automate_generate",
        _generate,
        "按 spec 生成 Power Automate 工程（flows/<name>/）并打包为旧版导入包/解决方案 zip；不调用真实 API",
        {"name": "工程名（小写短横线）", "spec": "对象或 JSON 字符串：trigger + actions",
         "target": "project / legacy / solution / all，默认 all",
         "output_dir": "输出目录，默认 flows",
         "publisher": "解决方案发布者（默认 GantryPublisher）",
         "prefix": "解决方案前缀（默认 new）", "version": "解决方案版本（默认 1.0.0.0）"},
    )
    registry.register(
        "power_automate_validate",
        _validate,
        "校验已生成的 Power Automate 工程目录：JSON 可解析、definition 必填键齐全",
        {"name": "工程名", "output_dir": "工程所在目录，默认 flows"},
    )
