# -*- coding: utf-8 -*-
"""Power Automate 直接 UI 操作插件（Playwright）。

和 `gantry/tool_plugins/power_automate.py` 的生成器不同：这里不生成 zip，
而是把 maker 门户当成一个可被模型观察/操作的界面：

    pa_probe → pa_goto → pa_snapshot → pa_click/pa_fill/pa_press → pa_snapshot

默认 `enabled=false`，必须在 `data/plugins.json` 的 `power-automate` 里显式打开；
`dry_run=true` 时 `pa_import` 只上传不点最终导入。浏览器复用 Gantry 的
EdgeProfile / 租户 SSO，不调用 Power Platform API。
"""
from __future__ import annotations

from typing import Any

from gantry import power_automate_ui as ui
from gantry.tools import ToolError

_DEFAULTS = {"enabled": False, "dry_run": True, "max_items": 80}
_TOOL_NAMES = ("pa_status", "pa_probe", "pa_snapshot", "pa_goto", "pa_click",
               "pa_fill", "pa_press", "pa_select", "pa_import")


def _cfg(ctx: Any, key: str) -> Any:
    overrides = ctx.state.get("overrides") or {}
    if key in overrides:
        return overrides[key]
    return (ctx.config or {}).get(key, _DEFAULTS.get(key))


def _require(ctx: Any) -> None:
    if not bool(_cfg(ctx, "enabled")):
        raise ToolError("Power Automate UI 操作未启用：在 data/plugins.json 里设置 "
                        "power-automate.enabled=true")


def _trim(snap: Any, query: str = "", max_items: int = 0) -> dict[str, Any]:
    """裁掉大快照：模型只需要当前可见的少量控件，不要把 500 个元素塞回上下文。"""
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
        "dry_run": bool(_cfg(ctx, "dry_run")),
        "max_items": int(_cfg(ctx, "max_items") or 80),
        "mode": "playwright-ui",
        "url": ui.PA_URL,
    }


def setup(ctx: Any) -> None:
    async def pa_status() -> dict[str, Any]:
        """查看 Power Automate UI 操作是否启用及 dry_run 配置。"""
        return await _status(ctx)

    async def pa_probe() -> dict[str, Any]:
        """打开 maker 门户，检查登录/许可/首页状态并截图。"""
        _require(ctx)
        return _trim(await ui.probe())

    async def pa_snapshot(query: str = "") -> dict[str, Any]:
        """读取当前 maker 页面可见控件（可按文本/aria/placeholder 过滤）。"""
        _require(ctx)
        return _trim(await ui.dom_snapshot(), query=query,
                     max_items=int(_cfg(ctx, "max_items") or 80))

    async def pa_goto(path: str = "/") -> dict[str, Any]:
        """在 maker 门户内导航到相对路径，例如 /flows 或 /solutions。"""
        _require(ctx)
        return _trim(await ui.goto_path(str(path or "/")))

    async def pa_click(text: str, role: str = "") -> dict[str, Any]:
        """按可见文本点击按钮/菜单/链接，返回点击后的页面快照。"""
        _require(ctx)
        return _trim(await ui.click_text(str(text or ""), role=str(role or "")))

    async def pa_fill(label: str, value: str = "", index: int = 0) -> dict[str, Any]:
        """按 label/placeholder/aria-label 填写输入框。"""
        _require(ctx)
        return _trim(await ui.fill_text(str(label or ""), str(value or ""),
                                        index=int(index or 0)))

    async def pa_press(key: str) -> dict[str, Any]:
        """按键（Enter/Tab/Escape/Control+A 等），用于提交/关闭对话框。"""
        _require(ctx)
        return _trim(await ui.press_key(str(key or "")))

    async def pa_select(label: str = "", value: str = "", index: int = 0) -> dict[str, Any]:
        """选择页面里的 <select>（按 label 或 value）。"""
        _require(ctx)
        return _trim(await ui.select_option(str(label or ""), str(value or ""),
                                            index=int(index or 0)))

    async def pa_import(zip_path: str, dry_run: bool | None = None) -> dict[str, Any]:
        """导入旧版 Power Automate 包；默认 dry_run=true 只上传不确认。"""
        _require(ctx)
        raw = str(zip_path or "").strip()
        if not raw:
            raise ToolError("zip_path 不能为空")
        try:
            path = ctx.host.registry._path(raw)
        except Exception as exc:                 # noqa: BLE001
            raise ToolError(f"zip_path 不在工作区/沙箱内: {exc}") from exc
        dry = bool(_cfg(ctx, "dry_run")) if dry_run is None else bool(dry_run)
        return _trim(await ui.import_run(str(path), dry_run=dry))

    async def api_status(request=None) -> dict[str, Any]:
        return await _status(ctx)

    async def api_settings(request=None) -> dict[str, Any]:
        try:
            body = await request.json() if request is not None else {}
        except Exception:
            body = {}
        overrides = dict(ctx.state.get("overrides") or {})
        for key in ("enabled", "dry_run", "max_items"):
            if key in body:
                overrides[key] = body[key]
        ctx.state["overrides"] = overrides
        ctx.save_state(overrides=overrides)
        return await _status(ctx)

    ctx.register_tool("pa_status", pa_status,
                      "查看 Power Automate UI 操作是否启用（enabled / dry_run）", {})
    ctx.register_tool("pa_probe", pa_probe,
                      "打开 make.powerautomate.com，检查登录/许可并截图", {})
    ctx.register_tool("pa_snapshot", pa_snapshot,
                      "读取当前 Power Automate 页面可见控件；query 可按文本/aria 过滤",
                      {"query": "过滤关键字（可选）"})
    ctx.register_tool("pa_goto", pa_goto,
                      "在 maker 门户内导航，例如 /flows、/solutions", {"path": "相对路径"})
    ctx.register_tool("pa_click", pa_click,
                      "按可见文本点击 Power Automate 页面上的按钮/菜单/链接",
                      {"text": "按钮或链接文本", "role": "可选 role，如 button/menuitem"})
    ctx.register_tool("pa_fill", pa_fill,
                      "按 label/placeholder/aria-label 填写 Power Automate 输入框",
                      {"label": "字段标签", "value": "要填入的值", "index": "同名控件序号，默认 0"})
    ctx.register_tool("pa_press", pa_press,
                      "在当前 Power Automate 页面按键（Enter/Tab/Escape 等）",
                      {"key": "Playwright 键名"})
    ctx.register_tool("pa_select", pa_select,
                      "选择 Power Automate 页面里的下拉框（按 label 或 value）",
                      {"label": "选项 label", "value": "选项 value", "index": "下拉框序号"})
    ctx.register_tool("pa_import", pa_import,
                      "导入旧版 Power Automate 包；默认 dry_run 只上传不确认",
                      {"zip_path": "工作区内的 zip 路径", "dry_run": "是否只上传不确认，默认取插件配置"},
                      approval=True)
    ctx.register_api("GET", "api/status", api_status)
    ctx.register_api("POST", "api/settings", api_settings)
