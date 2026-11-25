# -*- coding: utf-8 -*-
"""live-browser 插件：Agent 可调用的「打开网站」工具。

调用后网站在受管浏览器的新页里打开，对话页右侧的实时视图面板会自动展开，
用户能看着页面边分析边继续对话。抓取/互动能力后续版本再扩。
"""


def setup(ctx):
    async def _tool(url: str):
        from gantry import liveview
        try:
            r = await liveview.open_url(url)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
        return {"opened": r["url"], "title": r.get("title", ""),
                "hint": "页面已在受管浏览器打开，用户可在实时视图面板观看"}

    ctx.register_tool(
        "open_site", _tool,
        "在受管浏览器打开一个网站（用户在实时视图面板同步看到画面）。"
        "当用户要求分析/浏览某个网站时调用；只支持 http/https",
        {"url": "要打开的网站地址（http/https）"})
