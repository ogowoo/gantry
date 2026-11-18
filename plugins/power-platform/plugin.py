# -*- coding: utf-8 -*-
"""通用 Power Platform 门户 UI 操作插件（Playwright）。

一个插件覆盖 Power Apps / Power Automate / Power Pages / Copilot Studio /
Power BI / Power Platform Admin；域名白名单，复用 EdgeProfile 租户 SSO。
默认关闭，需在 data/plugins.json 的 `power-platform` 里显式打开。
"""
from __future__ import annotations

from typing import Any

from gantry import power_platform_ui as ui
from gantry.tools import ToolError

_DEFAULTS = {"enabled": False, "max_items": 80}
_TOOL_NAMES = ("pp_status", "pp_open", "pp_probe", "pp_snapshot", "pp_goto",
               "pp_click", "pp_fill", "pp_press", "pp_select")


def _cfg(ctx: Any, key: str) -> Any:
    overrides = ctx.state.get("overrides") or {}
    if key in overrides:
        return overrides[key]
    return (ctx.config or {}).get(key, _DEFAULTS.get(key))


def _require(ctx: Any) -> None:
    if not bool(_cfg(ctx, "enabled")):
        raise ToolError("Power Platform UI 操作未启用：在 data/plugins.json 里设置 "
                        "power-platform.enabled=true")


def _trim(snap: Any, query: str = "", max_items: int = 0) -> dict[str, Any]:
    if not isinstance(snap, dict):
        return {"ok": False, "error": "页面返回格式异常"}
    items = list(snap.get("items") or [])
    q = str(query or "").strip().casefold()
    if q:
        def hit(it: dict[str, Any]) -> bool:
            return any(q in str(it.get(k) or "").casefold()
                       for k in ("text", "aria", "placeholder", "href", "name", "role"))
        items = [it for it in items if isinstance(it, dict) and hit(it)]
    total = len(items)
    limit = int(max_items or _DEFAULTS["max_items"])
    snap["items"] = items[:max(1, limit)]
    snap["item_total"] = total
    snap.pop("frame_errors", None)
    return snap


async def _status(ctx: Any) -> dict[str, Any]:
    return {
        "enabled": bool(_cfg(ctx, "enabled")),
        "max_items": int(_cfg(ctx, "max_items") or 80),
        "mode": "playwright-ui",
        "portals": {k: {"label": v["label"], "url": v["url"]}
                    for k, v in ui.PORTALS.items()},
        "active": ui.active_portal(),
    }


def setup(ctx: Any) -> None:
    async def pp_status() -> dict[str, Any]:
        """查看通用 Power Platform UI 操作是否启用与可用门户。"""
        return await _status(ctx)

    async def pp_open(portal: str = "powerapps", path: str = "/") -> dict[str, Any]:
        """打开白名单门户：powerapps/powerautomate/powerpages/copilotstudio/powerbi/ppac。"""
        _require(ctx)
        return _trim(await ui.open_portal(str(portal or "powerapps"), str(path or "/")))

    async def pp_probe(portal: str = "powerapps") -> dict[str, Any]:
        """打开指定门户并检查登录/许可，截图到 data/pp-probe-<portal>.png。"""
        _require(ctx)
        return _trim(await ui.probe(str(portal or "powerapps")))

    async def pp_snapshot(query: str = "") -> dict[str, Any]:
        """读取当前门户页面可见控件（可按文本/aria 过滤）。"""
        _require(ctx)
        return _trim(await ui.dom_snapshot(), query=query,
                     max_items=int(_cfg(ctx, "max_items") or 80))

    async def pp_goto(path: str = "/", portal: str = "") -> dict[str, Any]:
        """在当前（或指定）门户内导航。"""
        _require(ctx)
        key = str(portal or "").strip() or ui.active_portal()
        if not key:
            raise ToolError("没有当前门户；先 pp_open，或传 portal")
        return _trim(await ui.open_portal(key, str(path or "/")))

    async def pp_click(text: str, role: str = "") -> dict[str, Any]:
        """按可见文本点击当前门户上的按钮/菜单/链接。"""
        _require(ctx)
        return _trim(await ui.click_text(str(text or ""), role=str(role or "")))

    async def pp_fill(label: str, value: str = "", index: int = 0) -> dict[str, Any]:
        """按 label/placeholder/aria-label 填写输入框。"""
        _require(ctx)
        return _trim(await ui.fill_text(str(label or ""), str(value or ""),
                                        index=int(index or 0)))

    async def pp_press(key: str) -> dict[str, Any]:
        """在当前门户页面按键（Enter/Tab/Escape 等）。"""
        _require(ctx)
        return _trim(await ui.press_key(str(key or "")))

    async def pp_select(label: str = "", value: str = "", index: int = 0) -> dict[str, Any]:
        """选择当前门户页面的 <select>。"""
        _require(ctx)
        return _trim(await ui.select_option(str(label or ""), str(value or ""),
                                            index=int(index or 0)))

    async def api_status(request=None) -> dict[str, Any]:
        return await _status(ctx)

    async def api_settings(request=None) -> dict[str, Any]:
        try:
            body = await request.json() if request is not None else {}
        except Exception:
            body = {}
        overrides = dict(ctx.state.get("overrides") or {})
        for key in ("enabled", "max_items"):
            if key in body:
                overrides[key] = body[key]
        ctx.state["overrides"] = overrides
        ctx.save_state(overrides=overrides)
        return await _status(ctx)

    ctx.register_tool("pp_status", pp_status,
                      "查看通用 Power Platform UI 操作是否启用与可用门户", {})
    ctx.register_tool("pp_open", pp_open,
                      "打开白名单门户：powerapps/powerautomate/powerpages/copilotstudio/powerbi/ppac",
                      {"portal": "门户 key", "path": "相对路径，默认 /"})
    ctx.register_tool("pp_probe", pp_probe,
                      "打开指定 Power Platform 门户并检查登录/许可",
                      {"portal": "门户 key，默认 powerapps"})
    ctx.register_tool("pp_snapshot", pp_snapshot,
                      "读取当前门户页面控件；query 可按文本/aria 过滤",
                      {"query": "过滤关键字（可选）"})
    ctx.register_tool("pp_goto", pp_goto,
                      "在当前（或指定）门户内导航",
                      {"path": "相对路径", "portal": "可选门户 key"})
    ctx.register_tool("pp_click", pp_click,
                      "按可见文本点击当前门户上的按钮/菜单/链接",
                      {"text": "按钮或链接文本", "role": "可选 role"})
    ctx.register_tool("pp_fill", pp_fill,
                      "按 label/placeholder/aria-label 填写当前门户输入框",
                      {"label": "字段标签", "value": "值", "index": "同名控件序号"})
    ctx.register_tool("pp_press", pp_press,
                      "在当前门户页面按键（Enter/Tab/Escape 等）",
                      {"key": "Playwright 键名"})
    ctx.register_tool("pp_select", pp_select,
                      "选择当前门户页面的下拉框",
                      {"label": "选项 label", "value": "选项 value", "index": "下拉框序号"})
    ctx.register_api("GET", "api/status", api_status)
    ctx.register_api("POST", "api/settings", api_settings)
