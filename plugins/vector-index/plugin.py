# -*- coding: utf-8 -*-
"""BGE-M3 向量索引插件（重型、可选）。

默认关闭。启用后：
- 用 OpenAI 兼容 /embeddings 调 BGE-M3（默认 1024 维），也可接 Infinity/llama.cpp；
- 读取 gantry KB 的 child chunks，建独立索引（不修改 kb.db 业务表）；
- 有 sqlite-vec 时走原生 KNN，否则用 numpy/纯 Python 余弦兜底；
- 提供完整管理面板、工具与 HTTP API（配置/重建/检索/重置/健康检查）。

依赖：
- 零 Python 新依赖（embeddings 走外部 HTTP）；
- 可选 sqlite-vec：`pip install sqlite-vec`；没有则 backend=numpy。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:18087/v1",
    "api_key": "",
    "model": "BAAI/bge-m3",
    "dim": 1024,
    "batch_size": 8,
    "timeout": 120,
    "index_backend": "auto",       # auto / numpy / sqlite-vec
    "top_k": 5,
}
_ALLOWED_CFG = {"base_url", "api_key", "model", "dim", "batch_size",
                "timeout", "index_backend", "top_k"}


class VectorIndexError(RuntimeError):
    """索引/嵌入/配置错误。"""


def _root(ctx) -> Path:
    return Path(ctx.host.root)


def _cfg(ctx) -> dict[str, Any]:
    c = dict(_DEFAULTS)
    c.update({k: v for k, v in (ctx.config or {}).items() if v not in (None, "")})
    state_cfg = (ctx.state or {}).get("config")
    if isinstance(state_cfg, dict):
        c.update({k: v for k, v in state_cfg.items() if v not in (None, "")})
    try:
        c["dim"] = max(8, int(c.get("dim") or 1024))
        c["batch_size"] = max(1, min(128, int(c.get("batch_size") or 8)))
        c["top_k"] = max(1, min(50, int(c.get("top_k") or 5)))
        c["timeout"] = max(5, float(c.get("timeout") or 120))
    except (TypeError, ValueError):
        raise VectorIndexError("dim/batch_size/timeout/top_k 必须是数字")
    return c


def _public_cfg(ctx) -> dict[str, Any]:
    c = _cfg(ctx)
    c["api_key"] = ""              # 管理面板不回显密钥
    c["api_key_set"] = bool(_cfg(ctx).get("api_key"))
    return c


def _embed(cfg: dict[str, Any], texts: list[str]) -> list[list[float]]:
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise VectorIndexError("未配置 embedding base_url")
    body = {"model": str(cfg.get("model") or ""), "input": list(texts)}
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + str(cfg["api_key"])
    req = urllib.request.Request(base + "/embeddings", data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout") or 120)) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise VectorIndexError(f"embedding HTTP {exc.code}: {detail}") from exc
    except Exception as exc:                     # noqa: BLE001
        raise VectorIndexError(f"embedding 端点不可用: {exc}") from exc
    data = raw.get("data") if isinstance(raw, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise VectorIndexError("embedding 返回格式/条数不正确")
    out = []
    for item in data:
        vec = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vec, list) or not vec:
            raise VectorIndexError("embedding 缺少向量")
        out.append([float(x) for x in vec])
    return out


def _cos(a: list[float], b: list[float]) -> float:
    n = sum(x * x for x in a) ** 0.5
    m = sum(y * y for y in b) ** 0.5
    if not n or not m:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (n * m)


class VectorIndex:
    def __init__(self, ctx):
        self.ctx = ctx
        self.cfg = _cfg(ctx)
        self.db_path = _root(ctx) / "data" / "vector-index" / "vector-index.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = "numpy"
        self._vec_ready = False
        self._conn = sqlite3.connect(str(self.db_path), timeout=20,
                                     check_same_thread=False)
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        self._ensure()

    def close(self):
        try:
            self._conn.close()
        except Exception:                        # noqa: BLE001
            pass

    def _ensure(self):
        c = self._conn
        c.execute("""CREATE TABLE IF NOT EXISTS meta(
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT UNIQUE, model TEXT, dim INTEGER, hash TEXT,
            text TEXT, title TEXT, heading TEXT, path TEXT,
            parent_id TEXT, parent_text TEXT, meta_json TEXT, vec_json TEXT, at REAL)""")
        cols = {r[1] for r in c.execute("PRAGMA table_info(meta)")}
        if "vec_json" not in cols:
            c.execute("ALTER TABLE meta ADD COLUMN vec_json TEXT")
        want = str(self.cfg.get("index_backend") or "auto").lower()
        can_vec = False
        if want in ("auto", "sqlite-vec", "sqlite_vec", "vec"):
            can_vec = self._try_vec()
        if want in ("sqlite-vec", "sqlite_vec", "vec") and not can_vec:
            raise VectorIndexError("index_backend=sqlite-vec，但 sqlite-vec 未安装/加载失败")
        self.backend = "sqlite-vec" if can_vec else "numpy"
        c.commit()

    def _try_vec(self) -> bool:
        try:
            import sqlite_vec  # noqa: PLC0415
        except Exception:                        # noqa: BLE001
            return False
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0("
                f"embedding float[{int(self.cfg['dim'])}] distance_metric=cosine)")
            self._vec_ready = True
            return True
        except Exception:                        # noqa: BLE001
            self._vec_ready = False
            return False

    def reset(self):
        with self._lock:
            self._reset_unlocked()

    def _reset_unlocked(self):
        c = self._conn
        try:
            c.execute("DROP TABLE IF EXISTS vec_chunks")
        except Exception:                        # noqa: BLE001
            pass
        c.execute("DROP TABLE IF EXISTS meta")
        c.commit()
        self._vec_ready = False
        self.backend = "numpy"
        self._ensure()

    def _kb_rows(self) -> list[dict[str, Any]]:
        kb = _root(self.ctx) / "data" / "kb.db"
        if not kb.is_file():
            raise VectorIndexError("找不到 data/kb.db；请先在知识库插件里 kb_reindex")
        con = sqlite3.connect(str(kb), timeout=20)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT id,path,doc_title,heading,text,parent_id,meta_json FROM chunks "
                "WHERE COALESCE(chunk_kind,'legacy') <> 'parent'").fetchall()
            parents = {r["id"]: r["text"] for r in con.execute(
                "SELECT id,text FROM chunks WHERE chunk_kind='parent'").fetchall()}
        except sqlite3.OperationalError as exc:
            raise VectorIndexError(f"kb.db 结构不支持父子块，请先升级/重建知识库: {exc}") from exc
        finally:
            con.close()
        out = []
        for r in rows:
            text = "\n".join(x for x in (r["doc_title"], r["heading"], r["text"]) if x)
            out.append({"chunk_id": r["id"], "text": text, "raw": r["text"],
                        "title": r["doc_title"] or "", "heading": r["heading"] or "",
                        "path": r["path"] or "", "parent_id": r["parent_id"] or "",
                        "parent_text": parents.get(r["parent_id"], "") if r["parent_id"] else "",
                        "meta_json": r["meta_json"] or "{}"})
        return out

    def build(self, force: bool = False, limit: int = 0) -> dict[str, Any]:
        with self._lock:
            return self._build_unlocked(force, limit)

    def _build_unlocked(self, force: bool = False, limit: int = 0) -> dict[str, Any]:
        self.cfg = _cfg(self.ctx)
        # 模型/维度变了：旧向量空间不可复用，必须整体重建。
        try:
            old_meta = self._conn.execute(
                "SELECT model, dim FROM meta LIMIT 1").fetchone()
            if old_meta and (old_meta["model"] != self.cfg.get("model")
                             or int(old_meta["dim"]) != int(self.cfg["dim"])):
                force = True
                self.reset()
        except Exception:                        # noqa: BLE001
            pass
        if force:
            self.reset()
        self._ensure()
        rows = self._kb_rows()
        if limit:
            rows = rows[:max(1, int(limit))]
        model = str(self.cfg.get("model") or "")
        dim = int(self.cfg.get("dim") or 1024)
        old = {r["chunk_id"]: r for r in self._conn.execute(
            "SELECT chunk_id, model, dim, hash, rowid FROM meta").fetchall()}
        todo = []
        for r in rows:
            h = hashlib.sha1(r["text"].encode("utf-8")).hexdigest()
            prev = old.get(r["chunk_id"])
            if (not force and prev and prev["model"] == model
                    and int(prev["dim"]) == dim and prev["hash"] == h):
                continue
            r["hash"] = h
            todo.append(r)
        made = 0
        batch = int(self.cfg["batch_size"])
        for i in range(0, len(todo), batch):
            part = todo[i:i + batch]
            vecs = _embed(self.cfg, [r["text"] or " " for r in part])
            for r, vec in zip(part, vecs):
                if len(vec) != dim:
                    raise VectorIndexError(f"embedding 维度 {len(vec)} != 配置 {dim}")
                self._upsert(r, vec)
                made += 1
            self._conn.commit()
        # 删除 KB 已不存在的块
        keep = {r["chunk_id"] for r in rows}
        for cid, prev in old.items():
            if cid not in keep:
                self._delete_row(int(prev["rowid"]))
        self._conn.commit()
        st = self.status()
        try:
            self.ctx.save_state(vector_index_last_build={
                "at": time.time(), "added": made, "chunks": st["indexed"]})
        except Exception:                        # noqa: BLE001
            pass
        return {**st, "added": made, "skipped": max(0, len(rows) - made)}

    def _delete_row(self, rowid: int):
        if self._vec_ready:
            try:
                self._conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (rowid,))
            except Exception:                    # noqa: BLE001
                pass
        self._conn.execute("DELETE FROM meta WHERE rowid=?", (rowid,))

    def _upsert(self, r: dict[str, Any], vec: list[float]):
        cur = self._conn.execute("SELECT rowid FROM meta WHERE chunk_id=?", (r["chunk_id"],)).fetchone()
        payload = (self.cfg.get("model"), int(self.cfg["dim"]), r["hash"], r["text"],
                   r["title"], r["heading"], r["path"], r["parent_id"],
                   r["parent_text"], r["meta_json"], json.dumps(vec), time.time())
        if cur:
            rowid = int(cur["rowid"])
            self._conn.execute(
                "UPDATE meta SET model=?,dim=?,hash=?,text=?,title=?,heading=?,path=?,"
                "parent_id=?,parent_text=?,meta_json=?,vec_json=?,at=? WHERE rowid=?",
                (*payload, rowid))
        else:
            c = self._conn.execute(
                "INSERT INTO meta(chunk_id,model,dim,hash,text,title,heading,path,parent_id,"
                "parent_text,meta_json,vec_json,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["chunk_id"], *payload))
            rowid = int(c.lastrowid)
        if self._vec_ready:
            try:
                import sqlite_vec  # noqa: PLC0415
                blob = sqlite_vec.serialize_float32(vec)
                self._conn.execute("DELETE FROM vec_chunks WHERE rowid=?", (rowid,))
                self._conn.execute("INSERT INTO vec_chunks(rowid, embedding) VALUES(?,?)",
                                   (rowid, blob))
            except Exception as exc:             # noqa: BLE001
                self._conn.execute("DELETE FROM meta WHERE rowid=?", (rowid,))
                raise VectorIndexError(f"sqlite-vec 写入失败: {exc}") from exc

    def search(self, query: str, top_k: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return self._search_unlocked(query, top_k)

    def _search_unlocked(self, query: str, top_k: int = 0) -> list[dict[str, Any]]:
        q = str(query or "").strip()
        if not q:
            return []
        self.cfg = _cfg(self.ctx)
        k = int(top_k or self.cfg["top_k"])
        qv = _embed(self.cfg, [q])[0]
        if len(qv) != int(self.cfg["dim"]):
            raise VectorIndexError(f"query 维度 {len(qv)} != 配置 {self.cfg['dim']}")
        if self._vec_ready:
            try:
                import sqlite_vec  # noqa: PLC0415
                rows = self._conn.execute(
                    "SELECT rowid, distance FROM vec_chunks WHERE embedding MATCH ? "
                    "ORDER BY distance LIMIT ?", (sqlite_vec.serialize_float32(qv), k)).fetchall()
                ids = [(int(r["rowid"]), 1.0 - float(r["distance"])) for r in rows]
            except Exception as exc:             # noqa: BLE001
                raise VectorIndexError(f"sqlite-vec 查询失败: {exc}") from exc
        else:
            ids = self._search_numpy(qv, k)
        out = []
        for rowid, score in ids:
            r = self._conn.execute("SELECT * FROM meta WHERE rowid=?", (rowid,)).fetchone()
            if not r:
                continue
            try:
                meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            except Exception:                    # noqa: BLE001
                meta = {}
            out.append({"chunk_id": r["chunk_id"], "title": r["title"],
                        "heading": r["heading"], "path": r["path"],
                        "text": r["text"], "parent_text": r["parent_text"],
                        "meta": meta, "score": round(score, 6),
                        "via": self.backend})
        return out

    def _search_numpy(self, qvec: list[float], k: int) -> list[tuple[int, float]]:
        rows = self._conn.execute("SELECT rowid, vec_json FROM meta").fetchall()
        scored = []
        for row in rows:
            try:
                vec = json.loads(row["vec_json"]) if row["vec_json"] else []
            except Exception:                    # noqa: BLE001
                continue
            if len(vec) != len(qvec):
                continue
            scored.append((int(row["rowid"]), _cos(qvec, vec)))
        scored.sort(key=lambda x: -x[1])
        return scored[:k]


    def status(self) -> dict[str, Any]:
        c = self._conn
        indexed = int(c.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"])
        kb = _root(self.ctx) / "data" / "kb.db"
        kb_chunks = 0
        if kb.is_file():
            try:
                con = sqlite3.connect(str(kb), timeout=10)
                kb_chunks = int(con.execute(
                    "SELECT COUNT(*) FROM chunks WHERE COALESCE(chunk_kind,'legacy') <> 'parent'"
                ).fetchone()[0])
                con.close()
            except Exception:                    # noqa: BLE001
                pass
        return {"model": self.cfg.get("model"), "dim": int(self.cfg["dim"]),
                "backend": self.backend, "indexed": indexed, "kb_chunks": kb_chunks,
                "db": str(self.db_path), "sqlite_vec": self._vec_ready,
                "last_build": (self.ctx.state or {}).get("vector_index_last_build")}


def _index(ctx) -> VectorIndex:
    vi = getattr(ctx, "_vector_index", None)
    if vi is None:
        vi = VectorIndex(ctx)
        ctx._vector_index = vi
    return vi


def _save_config(ctx, values: dict[str, Any]) -> dict[str, Any]:
    cur = _cfg(ctx)
    patch = {k: v for k, v in (values or {}).items() if k in _ALLOWED_CFG}
    if "api_key" in patch and not str(patch["api_key"] or "").strip():
        patch.pop("api_key", None)           # 空密码表示保留原密钥
    cur.update(patch)
    # 只保存能 JSON 化的值；api_key 留在插件 state（data/plugins.json 已 gitignore）
    ctx.save_state(config=cur)
    old = getattr(ctx, "_vector_index", None)
    if old is not None:
        try:
            old.close()
        except Exception:                    # noqa: BLE001
            pass
        ctx._vector_index = None
    return _public_cfg(ctx)


def _admin_data(ctx) -> dict[str, Any]:
    return {"values": _public_cfg(ctx), "status": _index(ctx).status()}


def setup(ctx) -> None:
    def _err(exc: Exception) -> dict[str, Any]:
        return {"ok": False, "error": str(exc)[:600]}

    def _status() -> dict[str, Any]:
        try:
            return {"ok": True, **_index(ctx).status()}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    def _build(force: bool = False, limit: int = 0) -> dict[str, Any]:
        try:
            return {"ok": True, **_index(ctx).build(force=bool(force), limit=int(limit or 0))}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    def _search(q: str, top_k: int = 0) -> dict[str, Any]:
        try:
            hits = _index(ctx).search(q, top_k=top_k)
            return {"ok": True, "query": q, "count": len(hits), "hits": hits}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    def _reset() -> dict[str, Any]:
        try:
            _index(ctx).reset()
            return {"ok": True, **_index(ctx).status()}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    def _health() -> dict[str, Any]:
        try:
            cfg = _cfg(ctx)
            vec = _embed(cfg, ["ping"])[0]
            return {"ok": True, "model": cfg["model"], "dim": len(vec),
                    "expected_dim": cfg["dim"], "backend": _index(ctx).backend}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    async def _a_build(force: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(_build, force)

    async def _a_reset() -> dict[str, Any]:
        return await asyncio.to_thread(_reset)

    async def _a_search(q: str, top_k: int = 0) -> dict[str, Any]:
        return await asyncio.to_thread(_search, q, top_k)

    ctx.register_tool("vector_index_status", _status,
                      "BGE-M3 vector index status (model/dim/backend/indexed chunks)", {})
    ctx.register_tool("vector_index_reindex", _a_build,
                      "Rebuild/incrementally update the BGE-M3 vector index (force=true full rebuild)",
                      {"force": "false=incremental, true=full rebuild"})
    ctx.register_tool("vector_index_search", _a_search,
                      "Search the local knowledge base with BGE-M3 vectors",
                      {"q": "user question", "top_k": "number of hits, defaults to config"})
    ctx.register_tool("vector_index_reset", _a_reset,
                      "Clear the vector index (knowledge base originals are kept)", {})

    def _api_status(request=None):
        return _status()

    def _api_admin(request=None):
        try:
            return {"ok": True, "values": _public_cfg(ctx), "status": _index(ctx).status()}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:600]}

    async def _api_config(request=None):
        try:
            values = await request.json() if request is not None else {}
            values = (values or {}).get("values", values)
            return {"ok": True, "values": _save_config(ctx, values or {})}
        except Exception as exc:                 # noqa: BLE001
            return _err(exc)

    async def _api_reindex(request=None):
        body = {}
        try:
            body = await request.json() if request is not None else {}
        except Exception:                        # noqa: BLE001
            body = {}
        return await asyncio.to_thread(_build, bool(body.get("force")), int(body.get("limit") or 0))

    async def _api_reset(request=None):
        return await asyncio.to_thread(_reset)

    async def _api_search(request=None):
        params = getattr(request, "query_params", {}) if request is not None else {}
        q = str(params.get("q") or params.get("query") or "")
        try:
            k = int(params.get("n") or params.get("top_k") or 0)
        except Exception:                        # noqa: BLE001
            k = 0
        return await asyncio.to_thread(_search, q, k)

    async def _api_health(request=None):
        return await asyncio.to_thread(_health)

    ctx.register_api("GET", "api/status", _api_status)
    ctx.register_api("GET", "api/admin", _api_admin)
    ctx.register_api("POST", "api/config", _api_config)
    ctx.register_api("POST", "api/reindex", _api_reindex)
    ctx.register_api("POST", "api/reset", _api_reset)
    ctx.register_api("GET", "api/search", _api_search)
    ctx.register_api("GET", "api/health", _api_health)

    ctx.register_panel({
        "id": "vector-index", "title": "BGE-M3 向量索引", "view": "admin",
        "api": {"method": "GET", "path": "api/admin"},
        "hint": "HTTP BGE-M3 → 独立索引；sqlite-vec 可用时走原生 KNN，否则 numpy；不修改 kb.db",
        "fields": [
            {"key": "base_url", "label": "Embedding 地址", "type": "text",
             "placeholder": "http://127.0.0.1:18087/v1"},
            {"key": "model", "label": "模型", "type": "text"},
            {"key": "dim", "label": "维度", "type": "number"},
            {"key": "batch_size", "label": "批大小", "type": "number"},
            {"key": "top_k", "label": "默认 Top-K", "type": "number"},
            {"key": "index_backend", "label": "索引后端", "type": "select",
             "options": [{"value": "auto", "label": "auto"},
                         {"value": "numpy", "label": "numpy"},
                         {"value": "sqlite-vec", "label": "sqlite-vec"}]},
            {"key": "api_key", "label": "API Key（留空保留）", "type": "password"},
        ],
        "actions": [
            {"id": "save", "label": "保存配置", "method": "POST", "path": "api/config"},
            {"id": "reindex", "label": "重建索引", "method": "POST", "path": "api/reindex"},
            {"id": "health", "label": "测试连接", "method": "GET", "path": "api/health",
             "send_values": False},
            {"id": "reset", "label": "清空索引", "method": "POST", "path": "api/reset",
             "danger": True, "confirm": "确定清空向量索引？知识库原文不会删除。"},
        ],
        "search": {"path": "api/search", "label": "检索测试",
                   "placeholder": "输入问题，回车/点击搜索"},
    })


def teardown(ctx) -> None:
    vi = getattr(ctx, "_vector_index", None)
    if vi is not None:
        try:
            vi.close()
        except Exception:                        # noqa: BLE001
            pass
        ctx._vector_index = None
    ctx.log("vector-index 已卸载")
