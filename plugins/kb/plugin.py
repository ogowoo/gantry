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
    # 向量层：fastembed = 进程内 ONNX 模型（默认，无需外部 server）；
    # auto = 端点可用就用 HTTP embedding；hash = 零依赖兜底；off = 关闭
    "embedding": {
        "backend": "fastembed",
        "model": "BAAI/bge-small-zh-v1.5",
        "cache_dir": "data/models/fastembed",
        "dim": 512,
        "local_files_only": False,
        "threads": 0,
        "base_url": "http://127.0.0.1:18085/v1",
        "model_path": "",
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
    """embedding 配置：默认 < config.json 的 kb.embedding < 插件 config.embedding。

    用户通常只在 config.json 里配一次；插件自身配置仍然可以覆盖（例如某个工作区
    想用 hash 兜底）。
    """
    base = dict(_DEFAULTS["embedding"])
    try:
        from gantry.bridge import CFG as _CFG

        glob = ((_CFG.get("kb") or {}).get("embedding") or {})
        if isinstance(glob, dict):
            base.update({k: v for k, v in glob.items() if v not in (None, "")})
    except Exception:                            # noqa: BLE001
        pass
    raw = (ctx.config or {}).get("embedding")
    if isinstance(raw, dict):
        base.update({k: v for k, v in raw.items() if v not in (None, "")})
    # cache_dir 相对路径按仓库根解析，避免服务从别的 cwd 启动时找不到模型
    cache = str(base.get("cache_dir") or "")
    if cache and not Path(cache).is_absolute():
        base["cache_dir"] = str(_root() / cache)
    return base


def _ocr_settings(ctx) -> dict[str, Any]:
    """kb.ocr 配置：磁盘 config.json > 内存 CFG > 默认（工程上以文件为准）。"""
    import json

    out: dict[str, Any] = {"engine": "rapidocr"}
    try:
        from gantry.bridge import CFG as _CFG

        out.update(dict((_CFG.get("kb") or {}).get("ocr") or {}))
    except Exception:                            # noqa: BLE001
        pass
    try:
        disk = json.loads((_root(ctx) / "config.json").read_text(encoding="utf-8"))
        out.update(dict((disk.get("kb") or {}).get("ocr") or {}))
    except Exception:                            # noqa: BLE001
        pass
    return out


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


def _after_change(ctx, *, embed: bool = True) -> dict[str, Any]:
    """文档增删后重建索引（FTS 必做；向量尽力而为），返回统计。"""
    out: dict[str, Any] = {}
    try:
        out["reindex"] = _kb(ctx).reindex()
    except Exception as exc:                     # noqa: BLE001
        out["reindex_error"] = str(exc)[:200]
    sem = _sem(ctx) if embed else None
    if sem is not None:
        try:
            out["embed"] = {k: sem.build().get(k) for k in ("vectorised", "chunks", "added")}
        except Exception as exc:                 # noqa: BLE001
            out["embed_error"] = str(exc)[:200]
    try:
        out["stats"] = _kb(ctx).stats()
    except Exception:                            # noqa: BLE001
        pass
    return out


def _safe_doc_path(kb, rel: str) -> Path:
    """只允许删知识库目录里的文件（防止 ../ 越界）。"""
    base = Path(kb.dir).resolve()
    target = (base / str(rel or "").replace("\\", "/")).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"只能删除知识库目录内的文件：{target}") from exc
    return target


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
        hits: list[dict] = []
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
        # 向量没建好 / 混合检索 0 命中时退回 FTS；否则 hits 未赋值会 500
        if not hits and chosen != "vector":
            hits = kb.search(q, max(1, min(n * 3, 30)))
            backend = "fts5-trigram"
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
    # OCR 引擎：优先读**磁盘上的** config.json（内存 CFG 是启动快照，改文件后 reload 不生效）
    try:
        from gantry import kb_ocr

        kb_ocr.configure(_ocr_settings(ctx))
        ctx.log(f"OCR 引擎: {kb_ocr.config().get('engine')} "
                f"(rapidocr 可用={kb_ocr.available()})")
        if bool(_ocr_settings(ctx).get("eager")):
            import threading

            def _warm_ocr() -> None:
                try:
                    res = kb_ocr.warm()
                    ctx.log(f"OCR 预热完成: {res}")
                except Exception as exc:         # noqa: BLE001
                    ctx.log(f"OCR 预热失败（不影响使用）: {exc}")

            threading.Thread(target=_warm_ocr, name="kb-ocr-warm", daemon=True).start()
            ctx.log("OCR 预热已排入后台（kb.ocr.eager=true）")
    except Exception as exc:                     # noqa: BLE001
        ctx.log(f"OCR 配置读取失败（忽略）: {exc}")

    ctx.register_tool("kb_reindex", kb_reindex,
                      "重建本地知识库索引（增量，按文件 mtime+size 判断）",
                      {"force": "true=忽略 mtime 全量重建"})
    ctx.register_tool("kb_stats", kb_stats, "知识库规模与索引状态", {})

    async def kb_add(text: str = "", title: str = "", url: str = "",
                     paths: list | None = None, category: str = "chat",
                     browser: bool = False) -> dict:
        """把内容加入知识库（会话里让模型自己整理后入库）。

        - `text`：整理好的 Markdown/文本；
        - `url`：抓网页正文；需要登录/Javascript 渲染的页面可设 `browser=True`；
        - `paths`：工作区/本地文件路径，自动按扩展名提取（docx/pdf/xlsx/pptx/图片OCR）；
        一次可以只给一种，也可以组合（例如 url + paths）。
        """
        from gantry import kb_ingest

        _ensure_index()
        kb = _kb(ctx)
        done: list[dict] = []
        errors: list[dict] = []
        if str(text or "").strip():
            try:
                path = kb_ingest.save_document(
                    kb.dir, title or "聊天里整理的条目", str(text), title=title,
                    source="chat", category=category)
                done.append({"ok": True, "kind": "text", "path": str(path),
                             "title": title or "聊天里整理的条目"})
            except Exception as exc:             # noqa: BLE001
                errors.append({"ok": False, "kind": "text", "error": str(exc)[:200]})
        if str(url or "").strip():
            try:
                res = await kb_ingest.ingest_url_async(
                    kb.dir, str(url),
                    use_browser=bool(browser), browser_fallback=not bool(browser))
                done.append({"ok": True, "kind": "url", **res})
            except Exception as exc:             # noqa: BLE001
                errors.append({"ok": False, "kind": "url", "error": str(exc)[:200]})
        for raw in (paths or []):
            try:
                fp = Path(str(raw)).expanduser()
                if not fp.is_absolute():
                    fp = _root(ctx) / str(raw)
                data = fp.read_bytes()
                res = kb_ingest.ingest_bytes(kb.dir, fp.name, data, title=fp.stem,
                                             source=str(fp))
                done.append({"ok": True, "kind": "file", **res})
            except Exception as exc:             # noqa: BLE001
                errors.append({"ok": False, "kind": "file", "path": str(raw),
                               "error": str(exc)[:200]})
        if not done and not errors:
            return {"ok": False, "error": "需要 text / url / paths 中至少一项"}
        out = _after_change(ctx)
        return {"ok": bool(done), "added": done, "errors": errors,
                "documents": (out.get("stats") or {}).get("documents")}

    ctx.register_tool(
        "kb_add", kb_add,
        "把内容加入本地知识库：整理好的 text / 网页 url / 文件 paths（自动提取 docx/pdf/xlsx/pptx/图片）；登录页用 browser=true",
        {"text": "整理后的正文（Markdown 更好）", "title": "标题（用于文件名与出处）",
         "url": "网页地址", "paths": "本地或工作区文件路径数组",
         "category": "分类，默认 chat", "browser": "是否用自动化浏览器抓取（适合登录页/JS 渲染）"})

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

    async def _api_import(request):
        """导入文档：{name, data_b64} 文件 / {url} 网页 / {text, title} 粘贴。"""
        import base64
        import binascii

        from gantry import kb_ingest

        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        body = body or {}
        kb = _kb(ctx)
        try:
            if body.get("url"):
                res = await kb_ingest.ingest_url_async(
                    kb.dir, str(body["url"]),
                    use_browser=bool(body.get("browser")),
                    browser_fallback=not bool(body.get("browser")))
            elif body.get("text"):
                title = str(body.get("title") or "").strip() or "粘贴的文本"
                text = str(body["text"])
                path = kb_ingest.save_document(kb.dir, title, text, title=title,
                                               source="pasted", category="pasted")
                res = {"path": str(path), "title": title, "chars": len(text)}
            elif body.get("data_b64"):
                name = str(body.get("name") or "upload.bin")
                try:
                    data = base64.b64decode(str(body["data_b64"]), validate=False)
                except (binascii.Error, ValueError) as exc:
                    return {"ok": False, "error": f"data_b64 不是合法 Base64：{exc}"}
                res = kb_ingest.ingest_bytes(kb.dir, name, data,
                                             title=str(body.get("title") or ""))
            else:
                return {"ok": False, "error": "需要 url / text / data_b64 之一"}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:300]}
        return {"ok": True, **res, **_after_change(ctx)}

    async def _api_delete(request):
        """删除一篇导入的文档：{path}（相对 data/kb 的路径）。"""
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        rel = str((body or {}).get("path") or "")
        if not rel:
            return {"ok": False, "error": "需要 path"}
        kb = _kb(ctx)
        try:
            target = _safe_doc_path(kb, rel)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)[:200]}
        if not target.is_file():
            return {"ok": False, "error": f"文件不存在：{rel}"}
        try:
            target.unlink()
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"删除失败：{exc}"}
        return {"ok": True, "deleted": rel, **_after_change(ctx)}

    async def _api_inbox(request):
        """看收件箱：把文件丢进 data/kb/inbox/，一次扫描即导入。"""
        kb = _kb(ctx)
        inbox = Path(kb.dir) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        files = sorted(f for f in inbox.iterdir() if f.is_file())
        return {"dir": str(inbox), "count": len(files),
                "files": [{"name": f.name, "size": f.stat().st_size} for f in files]}

    async def _api_inbox_scan(request):
        """导入收件箱里的全部文件，成功后移入 data/kb/inbox/imported/。"""
        from gantry import kb_ingest

        kb = _kb(ctx)
        inbox = Path(kb.dir) / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        done_dir = inbox / "imported"
        out: list[dict] = []
        for f in sorted(inbox.iterdir()):
            if not f.is_file():
                continue
            try:
                res = kb_ingest.ingest_bytes(kb.dir, f.name, f.read_bytes(),
                                             title=f.stem, source=f"inbox:{f.name}")
                done_dir.mkdir(exist_ok=True)
                f.replace(done_dir / f.name)
                out.append({"ok": True, **res})
            except Exception as exc:             # noqa: BLE001
                out.append({"ok": False, "name": f.name, "error": str(exc)[:200]})
        return {"imported": sum(1 for x in out if x.get("ok")), "items": out,
                **_after_change(ctx)}

    ctx.register_api("GET", "api/semantic", _api_semantic)
    async def _api_ocr(request):
        """看/切 OCR 引擎：GET 返回当前配置；POST {"engine": "rapidocr"|"xiaomi"}。"""
        from gantry import kb_ocr

        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        if isinstance(body, dict) and body.get("engine"):
            kb_ocr.configure(body)
        return {"ok": True, **kb_ocr.config(),
                "rapidocr": kb_ocr.available(),
                "xiaomi": kb_ocr.available_xiaomi(),
                "effective": kb_ocr.effective_engine(),
                "hint": "没装就用 python install.py --ocr（RapidOCR）"
                        " 或 --xiaomi（Xiaomi-OCR-0，需 torch+transformers）",
                "note": "切换只影响本进程；持久化请改 config.json 的 kb.ocr"}

    ctx.register_api("POST", "api/import", _api_import)
    ctx.register_api("GET", "api/ocr", _api_ocr)
    ctx.register_api("POST", "api/ocr", _api_ocr)
    ctx.register_api("POST", "api/delete", _api_delete)
    ctx.register_api("GET", "api/inbox", _api_inbox)
    ctx.register_api("POST", "api/inbox/scan", _api_inbox_scan)
    ctx.register_api("POST", "api/embed", _api_embed)
    ctx.register_api("GET", "api/vector-stats", _api_vector_stats)

    async def _api_local_models(request=None):
        """列出 data/models/ 里的本地 GGUF（给 Embedding local 后端选模型用）。"""
        from gantry.bridge import BASE as _BASE

        models_dir = Path(_BASE) / "data" / "models"
        if not models_dir.exists():
            return {"models": []}
        files = sorted(
            p for p in models_dir.rglob("*.gguf")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(models_dir).parts)
        )
        return {"models": [
            {"name": f.name,
             "path": str(f.relative_to(models_dir)),
             "full": str(f)}
            for f in files
        ]}

    ctx.register_api("GET", "api/local-models", _api_local_models)

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
