"""kb 插件：把"本地知识库"做成可插拔能力（原来是塞在 teams-bot 里的两个工具）。

## 为什么独立成插件

- 知识库是**通用能力**：WebUI 对话、Agent 任务、Teams 机器人、以后的 Telegram 都用得上，
  不该绑在某个渠道插件里；
- 与 `web-search` 插件对称：一个查本地（`data/kb/*.md`），一个查互联网，提示词里可并列；
- 索引与配置（目录、条数、阈值）都可以在 `data/plugins.json` 或面板里单独调。

## 数据流

    data/kb/**/*.md  ──reindex──▶  data/kb.db（FTS5 trigram，按 #/## 切块）
                                        │
                        kb_search(q) ───┴─▶ [{title, heading, path, text, score}]

检索是**字面匹配**（trigram + 覆盖率打分），零依赖、中文可用、毫秒级；向量检索属于可选增强，
要上再另写一个 `kb_semantic` 工具（本插件不含）。

## 工具 / 接口

| 工具 | 作用 |
| --- | --- |
| `kb_search(query, limit?)` | 查知识库，返回带出处的片段（`cite` 字段可直接引用） |
| `kb_reindex(force?)` | 增量重建索引（按 mtime+size 判断哪些文件变了） |
| `kb_stats()` | 文档数 / 分块数 / 字符数 / 是否 FTS |

接口：`GET api/search?q=&n=`、`POST api/reindex`、`GET api/stats`、`GET api/docs`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "dir": "data/kb",
    "max_results": 6,
    "min_score": 0.0,
    "auto_reindex": True,
}


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _cfg(ctx, key: str) -> Any:
    v = (ctx.config or {}).get(key)
    return _DEFAULTS.get(key) if v in (None, "") else v


def _kb(ctx):
    """按配置构造/缓存 KnowledgeBase（目录可配；索引库默认放 data/kb.db）。"""
    from gantry.kb import KnowledgeBase

    kb = getattr(ctx, "_kb", None)
    if kb is None:
        root = _root()
        sub = str(_cfg(ctx, "dir") or "data/kb").replace("\\", "/").strip("/")
        base = root / sub
        db = root / "data" / (base.name + ".db")
        kb = KnowledgeBase(root, db_path=db, kb_dir=base)
        ctx._kb = kb
    return kb


def _cite(hit: dict[str, Any]) -> str:
    title = str(hit.get("title") or "").strip() or "未命名"
    heading = str(hit.get("heading") or "").strip()
    return f"《{title}》" + (f" › {heading}" if heading else "")


def setup(ctx) -> None:
    from gantry.kb import KnowledgeBase  # noqa: F401  （确保模块可导入，报错早发现）

    def _ensure_index() -> None:
        """首次调用时把索引建起来（索引为空才建，避免每次搜索都扫盘）。"""
        if not bool(_cfg(ctx, "auto_reindex")):
            return
        kb = _kb(ctx)
        try:
            if int(kb.stats().get("documents") or 0) == 0:
                kb.reindex()
        except Exception as exc:                 # noqa: BLE001
            ctx.log(f"知识库首次建索引失败（继续）: {exc}")

    async def kb_search(query: str = "", limit: int = 0, min_score: float | None = None) -> dict:
        """查本地知识库（data/kb/*.md）。返回片段与出处，可直接引用。"""
        q = str(query or "").strip()
        if not q:
            return {"query": "", "count": 0, "hits": [],
                    "note": "query 为空：请给出要查的问题"}
        _ensure_index()
        kb = _kb(ctx)
        n = int(limit or _cfg(ctx, "max_results") or 6)
        threshold = float(_cfg(ctx, "min_score") or 0) if min_score is None else float(min_score)
        hits = kb.search(q, max(1, min(n * 3, 30)))
        if threshold > 0:
            hits = [h for h in hits if float(h.get("score") or 0) >= threshold]
        hits = hits[:n]
        for h in hits:
            h["cite"] = _cite(h)
        return {"query": q, "count": len(hits), "hits": hits,
                "backend": "fts5-trigram",
                "note": "" if hits else "知识库里没有匹配内容；可换关键词，或先 kb_reindex"}

    async def kb_reindex(force: bool = False) -> dict:
        """重建本地知识库索引（默认增量：只处理改过的文件）。"""
        return _kb(ctx).reindex(force=bool(force))

    async def kb_stats() -> dict:
        """知识库规模与索引状态（文档数/分块数/字符数/是否 FTS）。"""
        return _kb(ctx).stats()

    ctx.register_tool(
        "kb_search", kb_search,
        "查本地知识库（data/kb/**/*.md）：返回带出处的片段；IT 问答优先用它，不足再 web_search",
        {"query": "问题（中文整句即可）", "limit": "返回条数，默认取插件配置",
         "min_score": "最低相关度 0~1，默认取插件配置"})
    ctx.register_tool("kb_reindex", kb_reindex,
                      "重建本地知识库索引（增量，按文件 mtime+size 判断）",
                      {"force": "true=忽略 mtime 全量重建"})
    ctx.register_tool("kb_stats", kb_stats, "知识库规模与索引状态", {})

    # ── HTTP 接口（面板/排障用）──
    async def _api_search(request):
        q = ""
        n = 0
        try:
            params = getattr(request, "query_params", {}) or {}
            q = str(params.get("q") or params.get("query") or "")
            n = int(params.get("n") or 0)
        except Exception:                        # noqa: BLE001
            pass
        return await kb_search(q, n)

    async def _api_reindex(request):
        force = False
        try:
            body = await request.json()
            force = bool((body or {}).get("force"))
        except Exception:                        # noqa: BLE001
            pass
        return await kb_reindex(force=force)

    async def _api_stats(request=None):
        return await kb_stats()

    async def _api_docs(request=None):
        """列出已入库的文档（相对路径 + 分块数），便于确认哪些资料被收录。"""
        kb = _kb(ctx)
        con = kb.db()
        rows = con.execute(
            "SELECT path, COUNT(*) AS chunks, SUM(chars) AS chars "
            "FROM chunks GROUP BY path ORDER BY path").fetchall()
        return {"dir": str(kb.dir), "count": len(rows),
                "documents": [{"path": r["path"], "chunks": r["chunks"],
                               "chars": r["chars"]} for r in rows]}

    ctx.register_api("GET", "api/search", _api_search)
    ctx.register_api("POST", "api/reindex", _api_reindex)
    ctx.register_api("GET", "api/stats", _api_stats)
    ctx.register_api("GET", "api/docs", _api_docs)

    ctx.register_panel({"id": "kb-status", "title": "本地知识库", "view": "table",
                        "api": {"method": "GET", "path": "api/stats"},
                        "hint": "data/kb/*.md → FTS5 索引；放文档后 kb_reindex"})

    # 启动时顺手建一次索引（有文档才建，空目录只是空转）
    try:
        _ensure_index()
    except Exception:                            # noqa: BLE001
        pass
    ctx.log(f"kb 已就绪（目录 {_kb(ctx).dir}）")


def teardown(ctx) -> None:
    kb = getattr(ctx, "_kb", None)
    if kb is not None:
        try:
            kb.close()
        except Exception:                        # noqa: BLE001
            pass
