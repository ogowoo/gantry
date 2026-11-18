# -*- coding: utf-8 -*-
"""知识库可插拔 reranker。

默认关闭（零依赖）。开启后把召回结果交给外部 HTTP reranker
（Infinity / Jina / Cohere 兼容）精排；Gantry 侧不引入 PyTorch。

用法：
    kb.rerank = {"enabled": true, "base_url": "http://127.0.0.1:18087/v1",
                 "model": "BAAI/bge-reranker-v2-m3", "top_k": 3}
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Protocol


class RerankError(RuntimeError):
    """reranker 不可用 / 返回异常。"""


class Reranker(Protocol):
    name: str
    top_k: int

    def rerank(self, query: str, hits: list[dict[str, Any]],
               top_k: int = 0) -> list[dict[str, Any]]:
        ...


class NoopReranker:
    """不改变顺序；用于测试/强制关闭。"""

    name = "noop"
    top_k = 0

    def rerank(self, query: str, hits: list[dict[str, Any]],
               top_k: int = 0) -> list[dict[str, Any]]:
        return list(hits[:top_k]) if top_k else list(hits)


class HttpReranker:
    """OpenAI 风格 `/rerank` 的通用客户端。

    请求：
        {"model": "...", "query": "...", "documents": ["..."], "top_n": 3}
    兼容结果：
        {"results": [{"index": 0, "relevance_score": 0.9}]}
        {"data": [{"index": 0, "score": 0.9}]}
    """

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 top_k: int = 3, timeout: float = 30.0):
        self.base_url = str(base_url).rstrip("/")
        self.model = str(model or "")
        self.api_key = ***REDACTED_SECRET*** or "")
        self.top_k = int(top_k or 3)
        self.timeout = float(timeout or 30)
        self.name = f"http-rerank:{self.model or 'default'}"

    def _endpoint(self) -> str:
        if self.base_url.endswith("/rerank"):
            return self.base_url
        return self.base_url + "/rerank"

    def rerank(self, query: str, hits: list[dict[str, Any]],
               top_k: int = 0) -> list[dict[str, Any]]:
        if not hits:
            return []
        k = int(top_k or self.top_k or 3)
        docs = [str(h.get("parent_text") or h.get("text") or "") for h in hits]
        body = {"model": self.model, "query": str(query),
                "documents": docs, "top_n": k}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            ***REDACTED_SECRET***"Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(self._endpoint(),
                                     data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            raise RerankError(f"reranker HTTP {exc.code}: {detail}") from exc
        except Exception as exc:                 # noqa: BLE001
            raise RerankError(f"reranker 不可用: {exc}") from exc

        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise RerankError("reranker 返回格式不认识（缺 results/data）")
        scored: list[tuple[float, int]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                idx = int(row.get("index"))
            except Exception:                    # noqa: BLE001
                continue
            if idx < 0 or idx >= len(hits):
                continue
            raw = row.get("relevance_score", row.get("score", 0.0))
            try:
                score = float(raw)
            except (TypeError, ValueError):
                score = 0.0
            scored.append((score, idx))
        if not scored:
            raise RerankError("reranker 没有返回有效结果")
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, idx in scored[:max(1, k)]:
            hit = dict(hits[idx])
            hit["rerank_score"] = round(score, 6)
            out.append(hit)
        return out


def build_reranker(cfg: dict[str, Any] | None = None) -> Reranker | None:
    """enabled=false/缺省 → None；目前支持 backend=http。"""
    c = dict(cfg or {})
    if not bool(c.get("enabled")):
        return None
    backend = str(c.get("backend") or "http").lower()
    if backend in ("off", "none", "noop", "disabled", "false", "0"):
        return None
    if backend != "http":
        raise RerankError(f"不支持的 rerank backend: {backend!r}（目前支持 http）")
    base = str(c.get("base_url") or "").strip()
    model = str(c.get("model") or "").strip()
    if not base or not model:
        raise RerankError("rerank.enabled=true 需要 base_url 和 model")
    return HttpReranker(base, model, api_key=***REDACTED_SECRET***"api_key") or ""),
                        top_k=int(c.get("top_k") or 3),
                        timeout=float(c.get("timeout") or 30))
