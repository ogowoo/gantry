# -*- coding: utf-8 -*-
"""Power Apps maker 门户直接 UI 操作插件（Playwright）。

工具集与 `power-automate` 插件对齐，但门户是 make.powerapps.com：
看应用、打开/编辑、点击/填写/选择。默认 `enabled=false`，需在
`data/plugins.json` 的 `power-apps` 里显式打开；不调用 Power Platform API。
"""
from __future__ import annotations

from typing import Any

from gantry import power_apps_ui as ui
from gantry.tools import ToolError

_DEFAULTS = {"enabled": False, "max_items": 80}
_TOOL_NAMES = ("papp_status", "papp_probe", "papp_snapshot", "papp_goto",
               "papp_click", "papp_fill", "papp_press", "papp_select")


def _cfg(ctx: Any, key: str) -> Any:
    overrides = ctx.state.get("overrides") or {}
    if key in overrides:
        return overrides[key]
    return (ctx.config or {}).get(key, _DEFAULTS.get(key))


def _require(ctx: Any) -> None:
    if not bool(_cfg(ctx, "enabled")):
        raise ToolError("Power Apps UI 操作未启用：在 data/plugins.json 里设置 "
                        "power-apps.enabled=true")


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
        "url": ui.POWERAPPS_URL,
    }


def setup(ctx: Any) -> None:
    async def papp_status() -> dict[str, Any]:
        """查看 Power Apps UI 操作是否启用。"""
        return await _status(ctx)

    async def papp_probe() -> dict[str, Any]:
        """打开 make.powerapps.com，检查登录/许可并截图。"""
        _require(ctx)
        return _trim(await ui.probe())

    async def papp_snapshot(query: str = "") -> dict[str, Any]:
        """读取当前 Power Apps 页面可见控件（可按文本/aria 过滤）。"""
        _require(ctx)
        return _trim(await ui.dom_snapshot(), query=query,
                     max_items=int(_cfg(ctx, "max_items") or 80))

    async def papp_goto(path: str = "/") -> dict[str, Any]:
        """在 maker 门户内导航，例如 /apps、/solutions。"""
        _require(ctx)
        return _trim(await ui.goto_path(str(path or "/")))

    async def papp_click(text: str, role: str = "") -> dict[str, Any]:
        """按可见文本点击按钮/菜单/链接，返回点击后的页面快照。"""
        _require(ctx)
        return _trim(await ui.click_text(str(text or ""), role=str(role or "")))

    async def papp_fill(label: str, value: str = "", index: int = 0) -> dict[str, Any]:
        """按 label/placeholder/aria-label 填写输入框。"""
        _require(ctx)
        return _trim(await ui.fill_text(str(label or ""), str(value or ""),
                                        index=int(index or 0)))

    async def papp_press(key: str) -> dict[str, Any]:
        """按键（Enter/Tab/Escape 等），用于提交/关闭对话框。"""
        _require(ctx)
        return _trim(await ui.press_key(str(key or "")))

    async def papp_select(label: str = "", value: str = "", index: int = 0) -> dict[str, Any]:
        """选择页面里的 <select>（按 label 或 value）。"""
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

    ctx.register_tool("papp_status", papp_status,
                      "查看 Power Apps UI 操作是否启用", {})
    ctx.register_tool("papp_probe", papp_probe,
                      "打开 make.powerapps.com，检查登录/许可并截图", {})
    ctx.register_tool("papp_snapshot", papp_snapshot,
                      "读取当前 Power Apps 页面可见控件；query 可按文本/aria 过滤",
                      {"query": "过滤关键字（可选）"})
    ctx.register_tool("papp_goto", papp_goto,
                      "在 Power Apps maker 门户内导航，例如 /apps、/solutions",
                      {"path": "相对路径"})
    ctx.register_tool("papp_click", papp_click,
                      "按可见文本点击 Power Apps 页面上的按钮/菜单/链接",
                      {"text": "按钮或链接文本", "role": "可选 role，如 button/menuitem"})
    ctx.register_tool("papp_fill", papp_fill,
                      "按 label/placeholder/aria-label 填写 Power Apps 输入框",
                      {"label": "字段标签", "value": "要填入的值", "index": "同名控件序号，默认 0"})
    ctx.register_tool("papp_press", papp_press,
                      "在当前 Power Apps 页面按键（Enter/Tab/Escape 等）",
                      {"key": "Playwright 键名"})
    ctx.register_tool("papp_select", papp_select,
                      "选择 Power Apps 页面里的下拉框（按 label 或 value）",
                      {"label": "选项 label", "value": "选项 value", "index": "下拉框序号"})
    ctx.register_api("GET", "api/status", api_status)
    ctx.register_api("POST", "api/settings", api_settings)
