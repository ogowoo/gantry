"""本地知识库（KB）：`data/kb/**/*.md` → SQLite FTS5 全文索引。

为什么要自己写一个而不是复用 MEMORY：MEMORY 是**任务记忆**（一次任务里 agent 记住的事实），
而这里要的是**可长期维护的 IT 问答资料**（有人写 md、丢进目录、立刻能搜）。

设计要点：
- **trigram 分词**：FTS5 默认 unicode61 不切中文，"打印机连不上"会整句当一个 token 查不到。
  trigram 支持中英文子串匹配（≥3 字符），中文 FAQ 才真正可用。
- **按标题切块**：一个 md 可能很长，按 `#/##/###` 切成小块，命中后能定位到小节，
  回答里可以带「出处：《标题》 › 小节」。
- **增量重建**：按文件 mtime + size 判断是否变化，只重扫改过的文件（kb 目录以后会长大）。
- **失败可降级**：机器上的 SQLite 若没有 FTS5（极少见），退回 LIKE + 关键词打分，
  功能不中断，只是排序差一点。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

# 一个 chunk 的正文上限：太大了 FTS 命中后塞不进提示词，太小又丢上下文
MAX_CHUNK_CHARS = 1600

_FRONT_KEYS = ("title", "tags", "category", "source", "updated")


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """解析极简 front-matter：文件头 `---` 之间的 `key: value`（不引入 yaml 依赖）。"""
    meta: dict[str, Any] = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end < 0:
        return meta, text
    head, body = text[3:end], text[end + 4:]
    for line in head.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().lower()
        v = v.strip().strip('"').strip("'")
        if k in _FRONT_KEYS and v:
            if k == "tags":
                meta[k] = [x.strip() for x in re.split(r"[,，;；]", v) if x.strip()]
            else:
                meta[k] = v
    return meta, body.lstrip("\n")


def _split_sections(body: str) -> list[tuple[str, str]]:
    """按 markdown 标题切块 → [(标题路径, 正文)]。没有标题就整篇一块。"""
    lines = body.splitlines()
    out: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            out.append((" › ".join(t for _, t in stack) or "", text))
        buf.clear()

    for line in lines:
        m = re.match(r"^(#{1,4})\s+(.*\S)\s*$", line)
        if m:
            flush()
            level = len(m.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m.group(2).strip()))
        else:
            buf.append(line)
    flush()
    return out


def _chunks_for_file(path: Path, root: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_front_matter(raw)
    rel = str(path.relative_to(root)).replace("\\", "/")
    doc_title = meta.get("title") or path.stem
    tags = " ".join(meta.get("tags") or [])
    chunks: list[dict[str, Any]] = []
    for i, (heading, text) in enumerate(_split_sections(body)):
        # 超长小节再按段落硬切，避免单块过大
        for j, part in enumerate(_hard_split(text)):
            cid = hashlib.sha1(f"{rel}#{i}#{j}".encode()).hexdigest()[:16]
            chunks.append({
                "id": cid,
                "path": rel,
                "doc_title": doc_title,
                "heading": heading,
                "tags": tags,
                "text": part,
                "chars": len(part),
            })
    if not chunks:                     # 空文件也登记，避免每次都重扫
        chunks.append({"id": hashlib.sha1(rel.encode()).hexdigest()[:16], "path": rel,
                       "doc_title": doc_title, "heading": "", "tags": tags,
                       "text": "", "chars": 0})
    return chunks


def _hard_split(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    parts, buf, size = [], [], 0
    for para in text.split("\n\n"):
        if size + len(para) > MAX_CHUNK_CHARS and buf:
            parts.append("\n\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


# 查询里不参与打分的虚词（中文为主，英文少量）。宁可少列，别把实词误杀
_STOP = {
    "怎么", "如何", "怎样", "什么", "为什么", "哪里", "哪个", "请问", "一下", "可以",
    "能否", "是否", "需要", "应该", "这个", "那个", "我的", "我们", "你们", "他们",
    "the", "a", "an", "is", "are", "how", "what", "why", "can", "to", "of", "for",
}


def _query_terms(query: str) -> list[str]:
    """把查询拆成用于算覆盖率的实词（去掉虚词/单字/重复）。"""
    raw = re.split(r"[\s，,。！？?!；;：:、（）()\[\]{}\"'“”]+", (query or "").lower())
    out: list[str] = []
    for part in raw:
        part = part.strip()
        if len(part) < 2 or part in _STOP:
            continue
        if part not in out:
            out.append(part)
    if not out:                       # 全是虚词/单字时退回原串，避免彻底没分
        out = [t for t in [ (query or "").strip().lower() ] if t]
    return out[:8]


class KnowledgeBase:
    """`data/kb/**` 的全文索引。线程内使用；跨线程各自建实例即可（SQLite 自己会加锁）。"""

    def __init__(self, root: Path, db_path: Path | None = None):
        self.root = Path(root)
        self.dir = self.root / "data" / "kb"
        self.db_path = Path(db_path) if db_path else self.root / "data" / "kb.db"
        self._db: sqlite3.Connection | None = None
        self.fts = True

    # ── 连接与建表 ──
    def db(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self.db_path), timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS files(
            path TEXT PRIMARY KEY, mtime REAL, size INTEGER, chunks INTEGER)""")
        con.execute("""CREATE TABLE IF NOT EXISTS chunks(
            id TEXT PRIMARY KEY, path TEXT, doc_title TEXT, heading TEXT,
            tags TEXT, text TEXT, chars INTEGER)""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")
        try:
            con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                doc_title, heading, tags, text, tokenize='trigram')""")
            self.fts = True
        except sqlite3.OperationalError:
            # 老 SQLite 没有 trigram（<3.34）或没编 FTS5：降级为 LIKE 打分
            self.fts = False
            con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_text ON chunks(text)")
        con.commit()
        self._db = con
        return con

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            finally:
                self._db = None

    # ── 重建 ──
    def reindex(self, force: bool = False) -> dict[str, Any]:
        """扫描 kb 目录，只重建变过的文件。返回统计（给接口/日志用）。"""
        con = self.db()
        self.dir.mkdir(parents=True, exist_ok=True)
        known = {r["path"]: (r["mtime"], r["size"]) for r in con.execute("SELECT * FROM files")}
        seen: set[str] = set()
        added = updated = skipped = removed = 0
        for path in sorted(self.dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in (".md", ".markdown", ".txt"):
                continue
            rel = str(path.relative_to(self.dir)).replace("\\", "/")
            seen.add(rel)
            st = path.stat()
            old = known.get(rel)
            if not force and old and abs(old[0] - st.st_mtime) < 1e-6 and old[1] == st.st_size:
                skipped += 1
                continue
            chunks = _chunks_for_file(path, self.dir)
            con.execute("DELETE FROM chunks WHERE path=?", (rel,))
            if self.fts:
                con.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE path=?)", (rel,))
            for c in chunks:
                cur = con.execute(
                    "INSERT INTO chunks(id,path,doc_title,heading,tags,text,chars)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (c["id"], c["path"], c["doc_title"], c["heading"], c["tags"],
                     c["text"], c["chars"]))
                if self.fts:
                    con.execute(
                        "INSERT INTO chunks_fts(rowid,doc_title,heading,tags,text)"
                        " VALUES(?,?,?,?,?)",
                        (cur.lastrowid, c["doc_title"], c["heading"], c["tags"], c["text"]))
            con.execute("INSERT OR REPLACE INTO files(path,mtime,size,chunks) VALUES(?,?,?,?)",
                        (rel, st.st_mtime, st.st_size, len(chunks)))
            if old:
                updated += 1
            else:
                added += 1
        for rel in set(known) - seen:
            con.execute("DELETE FROM chunks WHERE path=?", (rel,))
            if self.fts:
                con.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT rowid FROM chunks WHERE path=?)", (rel,))
            con.execute("DELETE FROM files WHERE path=?", (rel,))
            removed += 1
        con.commit()
        total_chunks = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        return {"root": str(self.dir), "added": added, "updated": updated,
                "skipped": skipped, "removed": removed, "fts": self.fts,
                "documents": len(seen), "chunks": total_chunks,
                "at": time.strftime("%Y-%m-%d %H:%M:%S")}

    # ── 检索 ──
    def search(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        """返回 [{title, heading, tags, path, text, score}]，score 越大越相关（0~1）。"""
        q = (query or "").strip()
        if not q:
            return []
        con = self.db()
        rows: list[sqlite3.Row] = []
        if self.fts:
            rows = self._search_fts(con, q, limit)
        if not rows:
            rows = self._search_like(con, q, limit)
        hits = [self._row_to_hit(r) for r in rows]
        self._score(q, hits)
        hits.sort(key=lambda h: -h["score"])
        return hits[:max(1, limit)]

    def _search_fts(self, con: sqlite3.Connection, q: str, limit: int) -> list[sqlite3.Row]:
        """trigram 需要 ≥3 字符的片段；把 query 拆成片段分别查，再按命中数打分。"""
        frags = [f for f in re.split(r"[\s，,。！？?！;；:：]+", q) if len(f) >= 3]
        if not frags:
            return []
        match = " OR ".join(f'"{f}"' for f in frags[:6])
        try:
            return con.execute(
                "SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts"
                " JOIN chunks c ON c.rowid = chunks_fts.rowid"
                " WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, max(1, limit))).fetchall()
        except sqlite3.OperationalError:
            return []

    def _search_like(self, con: sqlite3.Connection, q: str, limit: int) -> list[sqlite3.Row]:
        """降级：把 query 拆成关键词，按命中个数排序（中文按 2 字滑窗也算词）。"""
        words = [w for w in re.split(r"[\s，,。！？?！;；:：]+", q) if w]
        if not words:
            return []
        conds, params = [], []
        for w in words[:6]:
            for cand in {w, w[:2]} if len(w) > 2 else {w}:
                conds.append("(text LIKE ? OR doc_title LIKE ? OR heading LIKE ?)")
                params.extend([f"%{cand}%"] * 3)
        sql = ("SELECT *, NULL AS rank FROM chunks WHERE " + " OR ".join(conds) +
               " LIMIT ?")
        try:
            rows = con.execute(sql, (*params, max(1, limit) * 3)).fetchall()
        except sqlite3.OperationalError:
            return []
        scored = []
        for r in rows:
            text = (r["text"] or "") + (r["doc_title"] or "") + (r["heading"] or "")
            hits = sum(1 for w in words if w in text)
            scored.append((hits, r))
        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    @staticmethod
    def _row_to_hit(r: sqlite3.Row) -> dict[str, Any]:
        rank = r["rank"] if "rank" in r.keys() else None
        return {"id": r["id"], "title": r["doc_title"], "heading": r["heading"],
                "tags": r["tags"], "path": r["path"], "text": r["text"],
                "score": 0.0, "_rank": None if rank is None else float(rank)}

    @staticmethod
    def _score(query: str, hits: list[dict[str, Any]]) -> None:
        """覆盖率为主、bm25 名次为辅；结果写回 hit["score"]（0~1）。"""
        terms = _query_terms(query)
        ranks = [abs(h["_rank"]) for h in hits if h.get("_rank") is not None]
        lo = min(ranks) if ranks else 0.0
        hi = max(ranks) if ranks else 0.0
        for h in hits:
            hay = " ".join(str(h.get(k) or "") for k in ("title", "heading", "tags", "text"))
            hay = hay.lower()
            cov = (sum(1 for t in terms if t in hay) / len(terms)) if terms else 0.0
            rk = h.get("_rank")
            if rk is None:
                pos = 0.55                    # LIKE 降级：名次分给中性值
            elif hi - lo < 1e-9:
                pos = 1.0
            else:
                pos = 1.0 - (abs(rk) - lo) / (hi - lo)
            h["score"] = round(0.75 * cov + 0.25 * pos, 4)
            h.pop("_rank", None)

    # ── 给模型看的整段回答素材 ──
    def context_for(self, query: str, limit: int = 4, max_chars: int = 6000) -> str:
        hits = self.search(query, limit)
        if not hits:
            return ""
        parts = []
        used = 0
        for h in hits:
            src = f"《{h['title']}》" + (f" › {h['heading']}" if h["heading"] else "")
            block = f"### {src}\n{h['text'].strip()}"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts)

    def stats(self) -> dict[str, Any]:
        con = self.db()
        files = con.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        chunks = con.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        chars = con.execute("SELECT COALESCE(SUM(chars),0) AS n FROM chunks").fetchone()["n"]
        return {"dir": str(self.dir), "documents": files, "chunks": chunks,
                "chars": chars, "fts": self.fts, "index": str(self.db_path)}


def kb_meta_json(meta: dict[str, Any]) -> str:
    return json.dumps(meta, ensure_ascii=False)
