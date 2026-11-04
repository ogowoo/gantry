"""知识库的向量检索层：把分块 embedding 进 SQLite，做语义召回，再与 FTS 结果融合。

## 为什么加这一层

`gantry/kb.py` 是**字面匹配**（FTS5 trigram + 覆盖率）：快、零依赖、中文可用，但同义改写会漏
（"公司网络访问不了外部网站"未必命中只写"隧道"的文档）。向量召回补这个短板。

## 设计

- **后端可插拔**：`HttpEmbedder` 打任意 OpenAI 兼容 `/embeddings`（本机 `llama-server
  --embeddings` 跑的是 Qwen3-Embedding-0.6B，1024 维）；`HashEmbedder` 是零依赖兜底
  （字符 n-gram hashing），端点不可用或跑单测时用。
- **索引存在同一个 `data/kb.db`**：`chunks_vec(chunk_id, model, dim, vec BLOB)`，
  模型或维度变了会整体重建（避免混用不同向量空间）。
- **只重算缺失的分块**：按 chunk_id 对齐，新增/改动的文档只补新块。
- **融合**：`score = w_vec × 余弦归一化 + w_cov × 词面覆盖率`（默认 0.75 / 0.25）。
  纯余弦对专有词不敏感，词面覆盖率把"分机 8000""RITM 编号"这类精确串拉回来。

## 用法

    from gantry.kb import KnowledgeBase
    from gantry.kb_semantic import SemanticIndex, HttpEmbedder

    kb = KnowledgeBase(Path("."))
    idx = SemanticIndex(kb, HttpEmbedder("http://127.0.0.1:18085/v1", "qwen3-embed"))
    idx.build()                      # 增量建向量
    idx.search("上不了外网", 5)       # 纯向量
    idx.hybrid("上不了外网", 5)       # 向量 + 词面 融合（推荐）
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Protocol

_WS = re.compile(r"[\s，,。！？?!；;：:、（）()\[\]{}]+")


# ── 嵌入后端 ───────────────────────────────────────────────────────────────
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class HttpEmbedder:
    """任意 OpenAI 兼容 `/embeddings` 端点（llama.cpp `--embeddings` / Ollama / FLM）。"""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout: float = 60.0, dim: int = 0):
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model)
        self.api_key = str(api_key or "")
        self.timeout = float(timeout)
        self.dim = int(dim or 0)
        self.name = f"http:{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        body = {"model": self.model, "input": list(texts)}
        req = urllib.request.Request(
            self.base_url + "/embeddings",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": "Bearer " + self.api_key} if self.api_key else {})})
        try:
            raw = urllib.request.urlopen(req, timeout=self.timeout).read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:200].decode("utf-8", "replace")
            raise RuntimeError(f"embedding 端点返回 {exc.code}: {detail}") from exc
        except Exception as exc:                 # noqa: BLE001
            raise RuntimeError(f"embedding 端点不可用：{exc}") from exc
        data = json.loads(raw)
        rows = data.get("data")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("embedding 响应里没有 data（端点不是 OpenAI 兼容格式？）")
        vecs = [list(map(float, r.get("embedding") or [])) for r in rows]
        if not vecs or not vecs[0]:
            raise RuntimeError("embedding 响应里的向量为空")
        self.dim = len(vecs[0])
        return vecs


class HashEmbedder:
    """零依赖兜底：字符 n-gram hashing（不训练、不联网，单测与离线可用）。

    对中文按 2-gram、英文按词切，哈希到固定维度后 L2 归一化。语义能力远不如真模型，
    但比"完全没向量"好，而且行为确定，适合做回归测试的锚点。
    """

    def __init__(self, dim: int = 512):
        self.dim = int(dim)
        self.name = f"hash:{self.dim}"

    def _features(self, text: str) -> Iterable[str]:
        t = str(text or "").lower()
        for part in _WS.split(t):
            if not part:
                continue
            if re.search(r"[\u4e00-\u9fff]", part):
                for i in range(len(part) - 1):
                    yield part[i:i + 2]
                yield part
            else:
                yield part

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                h = int(hashlib.md5(feat.encode("utf-8")).hexdigest()[:8], 16)
                vec[h % self.dim] += 1.0
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


# ── 向量索引 ───────────────────────────────────────────────────────────────
class SemanticIndex:
    """知识库分块的向量索引（存在 kb.db 里；只算缺失的分块）。"""

    def __init__(self, kb, embedder: Embedder, table: str = "chunks_vec"):
        self.kb = kb
        self.embedder = embedder
        self.table = table
        self._ensure_table()

    # ── 存储 ──
    def _ensure_table(self) -> None:
        con = self.kb.db()
        con.execute(f"""CREATE TABLE IF NOT EXISTS {self.table}(
            chunk_id TEXT PRIMARY KEY, model TEXT, dim INTEGER, vec BLOB, at REAL)""")
        con.commit()

    def stats(self) -> dict[str, Any]:
        con = self.kb.db()
        row = con.execute(
            f"SELECT COUNT(*) AS n, MAX(dim) AS dim FROM {self.table} WHERE model=?",
            (self.embedder.name,)).fetchone()
        total = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return {"model": self.embedder.name,
                "vectorised": int(row["n"] or 0), "dim": int(row["dim"] or 0),
                "chunks": int(total), "complete": int(row["n"] or 0) >= int(total)}

    def _model_rows(self) -> list[sqlite3.Row]:
        con = self.kb.db()
        return con.execute(
            f"SELECT chunk_id, dim, vec FROM {self.table} WHERE model=?",
            (self.embedder.name,)).fetchall()

    def _missing(self) -> list[dict[str, Any]]:
        con = self.kb.db()
        rows = con.execute(
            "SELECT id, doc_title, heading, text FROM chunks").fetchall()
        have = {r["chunk_id"] for r in self._model_rows()}
        return [{"id": r["id"], "text": "\n".join(
            x for x in (r["doc_title"], r["heading"], r["text"]) if x)}
            for r in rows if r["id"] not in have]

    # ── 建索引 ──
    def build(self, force: bool = False, batch: int = 16,
              progress=None) -> dict[str, Any]:
        """增量建向量；force=True 先清掉本模型的旧向量再全量重算。"""
        con = self.kb.db()
        if force:
            con.execute(f"DELETE FROM {self.table} WHERE model=?", (self.embedder.name,))
            con.commit()
        todo = self._missing()
        done = 0
        started = time.time()
        for i in range(0, len(todo), max(1, batch)):
            part = todo[i:i + max(1, batch)]
            vecs = self.embedder.embed([p["text"] or " " for p in part])
            for item, vec in zip(part, vecs):
                con.execute(
                    f"INSERT OR REPLACE INTO {self.table}"
                    "(chunk_id, model, dim, vec, at) VALUES(?,?,?,?,?)",
                    (item["id"], self.embedder.name, len(vec),
                     json.dumps(vec), time.time()))
            con.commit()
            done += len(part)
            if progress:
                progress(done, len(todo))
        st = self.stats()
        return {**st, "added": done, "seconds": round(time.time() - started, 2),
                "backend": self.embedder.name}

    # ── 检索 ──
    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """纯向量召回：返回 [{..., score}]，score 是余弦（-1~1 归一化到 0~1）。"""
        q = str(query or "").strip()
        if not q:
            return []
        rows = self._model_rows()
        if not rows:
            return []
        qvec = self.embedder.embed([q])[0]
        con = self.kb.db()
        scored = []
        for r in rows:
            try:
                vec = json.loads(r["vec"])
            except Exception:                    # noqa: BLE001
                continue
            if len(vec) != len(qvec):
                continue
            scored.append((_cosine(qvec, vec), r["chunk_id"]))
        scored.sort(reverse=True)
        out = []
        for cos, cid in scored[:max(1, limit)]:
            row = con.execute(
                "SELECT id, path, doc_title, heading, tags, text FROM chunks WHERE id=?",
                (cid,)).fetchone()
            if not row:
                continue
            out.append({"id": row["id"], "title": row["doc_title"],
                        "heading": row["heading"], "tags": row["tags"],
                        "path": row["path"], "text": row["text"],
                        "score": round((cos + 1.0) / 2.0, 4), "cos": round(cos, 4),
                        "via": "vector"})
        return out

    def hybrid(self, query: str, limit: int = 5, w_vec: float = 0.75,
               w_cov: float = 0.25) -> list[dict[str, Any]]:
        """向量 + 词面融合：`w_vec × cos + w_cov × 覆盖率`（缺失的一侧按 0 计）。

        纯余弦对专有词不敏感（"分机 8000"这种），词面覆盖率把它拉回来；
        反过来只靠词面会漏同义改写。两边都要。
        """
        q = str(query or "").strip()
        if not q:
            return []
        from .kb import _query_terms

        terms = _query_terms(q)
        vec_hits = {h["id"]: h for h in self.search(q, max(limit * 4, 20))}
        try:
            fts_hits = self.kb.search(q, max(limit * 4, 20))
        except Exception:                        # noqa: BLE001
            fts_hits = []
        fts_map = {h["id"]: h for h in fts_hits}
        merged: dict[str, dict[str, Any]] = {}
        for cid in set(vec_hits) | set(fts_map):
            vh = vec_hits.get(cid) or {}
            fh = fts_map.get(cid) or {}
            base = vh or fh
            text = " ".join(str(base.get(k) or "") for k in ("title", "heading", "text"))
            coverage = (sum(1 for t in terms if t in text.lower()) / len(terms)) if terms else 0.0
            vec = float(vh.get("score") or 0.0)
            merged[cid] = {
                "id": cid, "title": base.get("title"), "heading": base.get("heading"),
                "tags": base.get("tags"), "path": base.get("path"),
                "text": base.get("text"),
                "score": round(w_vec * vec + w_cov * coverage, 4),
                "vec": round(vec, 4), "coverage": round(coverage, 4),
                "via": "both" if (vh and fh) else ("vector" if vh else "fts"),
            }
        out = sorted(merged.values(), key=lambda h: -h["score"])
        return out[:max(1, limit)]


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (na * nb)


def build_embedder(cfg: dict[str, Any] | None = None) -> Embedder | None:
    """按配置造嵌入后端：backend = auto|http|hash|off（auto = 端点可用就用 HTTP）。"""
    c = dict(cfg or {})
    backend = str(c.get("backend") or "auto").lower()
    if backend in ("off", "none", "disabled", "false", "0"):
        return None
    if backend == "hash":
        return HashEmbedder(int(c.get("dim") or 512))
    base = str(c.get("base_url") or "http://127.0.0.1:18085/v1")
    model = str(c.get("model") or "qwen3-embed")
    emb = HttpEmbedder(base, model, api_key=str(c.get("api_key") or ""),
                       dim=int(c.get("dim") or 0))
    if backend == "http":
        return emb
    # auto：探一下端点（失败就返回 None，调用方退回 FTS）
    try:
        emb.embed(["ping"])
        return emb
    except Exception:                            # noqa: BLE001
        return None


def index_for(kb, cfg: dict[str, Any] | None = None) -> SemanticIndex | None:
    emb = build_embedder(cfg)
    if emb is None:
        return None
    return SemanticIndex(kb, emb)
