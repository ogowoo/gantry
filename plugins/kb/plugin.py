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
    # 向量层：auto = 端点可用就用 HTTP embedding（本机 llama-server --embeddings），
    # 不可用自动退回纯 FTS；hash = 零依赖兜底；off = 关闭
    "embedding": {
        "backend": "auto",
        "base_url": "http://127.0.0.1:18085/v1",
        "model": "qwen3-embed",
        "dim": 0,
        "w_vec": 0.75,
        "w_cov": 0.25,
    },
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


def _emb_cfg(ctx) -> dict[str, Any]:
    """合并插件配置里的 embedding 段（缺省用 _DEFAULTS）。"""
    raw = (ctx.config or {}).get("embedding")
    base = dict(_DEFAULTS["embedding"])
    if isinstance(raw, dict):
        base.update({k: v for k, v in raw.items() if v not in (None, "")})
    return base


def _sem(ctx):
    """按配置构造/缓存 SemanticIndex；后端不可用/关闭时返回 None。"""
    from gantry.kb_semantic import index_for

    if getattr(ctx, "_sem", "missing") == "missing":
        try:
            ctx._sem = index_for(_kb(ctx), _emb_cfg(ctx))
        except Exception as exc:                 # noqa: BLE001
            ctx.log(f"向量层初始化失败（退回 FTS）: {exc}")
            ctx._sem = None
    return getattr(ctx, "_sem", None)


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

    async def kb_search(query: str = "", limit: int = 0, min_score: float | None = None,
                        mode: str = "auto") -> dict:
        """查本地知识库（data/kb/*.md）。mode=auto 时有向量索引就混合检索，否则纯 FTS。"""
        q = str(query or "").strip()
        if not q:
            return {"query": "", "count": 0, "hits": [],
                    "note": "query 为空：请给出要查的问题"}
        _ensure_index()
        kb = _kb(ctx)
        n = int(limit or _cfg(ctx, "max_results") or 6)
        threshold = float(_cfg(ctx, "min_score") or 0) if min_score is None else float(min_score)
        chosen = str(mode or "auto").lower()
        sem = _sem(ctx) if chosen in ("auto", "vector", "hybrid") else None
        backend = "fts5-trigram"
        if sem is not None and chosen in ("auto", "hybrid"):
            e = _emb_cfg(ctx)
            st = sem.stats()
            if st["vectorised"] == 0 and bool(_cfg(ctx, "auto_reindex")):
                try:
                    sem.build()
                    st = sem.stats()
                except Exception as exc:         # noqa: BLE001
                    ctx.log(f"自动建向量失败（退回 FTS）: {exc}")
            if st["vectorised"] > 0:
                hits = sem.hybrid(q, n * 2,
                                  w_vec=float(e.get("w_vec") or 0.75),
                                  w_cov=float(e.get("w_cov") or 0.25))
                backend = f"hybrid({sem.embedder.name})"
        elif sem is not None and chosen == "vector":
            hits = sem.search(q, n * 2)
            backend = f"vector({sem.embedder.name})"
        else:
            hits = kb.search(q, max(1, min(n * 3, 30)))
        if threshold > 0:
            hits = [h for h in hits if float(h.get("score") or 0) >= threshold]
        hits = hits[:n]
        for h in hits:
            h["cite"] = _cite(h)
            h.pop("id", None)
        return {"query": q, "count": len(hits), "hits": hits, "backend": backend,
                "note": "" if hits else "知识库里没有匹配内容；可换关键词，或先 kb_reindex"}

    async def kb_reindex(force: bool = False) -> dict:
        """重建本地知识库索引（默认增量：只处理改过的文件）。"""
        return _kb(ctx).reindex(force=bool(force))

    async def kb_embed(force: bool = False) -> dict:
        """建/更新知识库的**向量**索引（语义检索用；后端不可用时明确报错）。"""
        sem = _sem(ctx)
        if sem is None:
            return {"ok": False,
                    "error": "没有可用的 embedding 后端（配置 kb.embedding.backend="
                             "auto/http/hash，或把 backend 设成 hash 走零依赖兜底）",
                    "config": _emb_cfg(ctx)}
        try:
            res = sem.build(force=bool(force))
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:300], "config": _emb_cfg(ctx)}
        return {"ok": True, **res}

    async def kb_stats() -> dict:
        """知识库规模与索引状态（文档数/分块数/字符数/是否 FTS）。"""
        return _kb(ctx).stats()

    ctx.register_tool(
        "kb_search", kb_search,
        "查本地知识库（data/kb/**/*.md）：返回带出处的片段；IT 问答优先用它，不足再 web_search",
        {"query": "问题（中文整句即可）", "limit": "返回条数，默认取插件配置",
         "min_score": "最低相关度 0~1，默认取插件配置"})
    ctx.register_tool("kb_embed", kb_embed,
                      "建/更新知识库向量索引（语义检索用）；离线可用 backend=hash",
                      {"force": "true=清掉本模型旧向量后全量重算"})
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

    async def _api_semantic(request):
        q = ""
        n = 0
        try:
            params = getattr(request, "query_params", {}) or {}
            q = str(params.get("q") or params.get("query") or "")
            n = int(params.get("n") or 0)
        except Exception:                        # noqa: BLE001
            pass
        return await kb_search(q, n, None, "vector")

    async def _api_embed(request):
        force = False
        try:
            body = await request.json()
            force = bool((body or {}).get("force"))
        except Exception:                        # noqa: BLE001
            pass
        return await kb_embed(force)

    async def _api_vector_stats(request=None):
        sem = _sem(ctx)
        if sem is None:
            return {"available": False, "config": _emb_cfg(ctx)}
        return {"available": True, "config": _emb_cfg(ctx), **sem.stats()}

    ctx.register_api("GET", "api/semantic", _api_semantic)
    ctx.register_api("POST", "api/embed", _api_embed)
    ctx.register_api("GET", "api/vector-stats", _api_vector_stats)

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
