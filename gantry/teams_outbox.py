"""Teams 发送幂等箱（SQLite）：同一 (conversation_id, message_id) 只允许发送一次。

## 为什么需要

发送不是一个原子操作：打开会话 → 校验身份 → POST/点发送 → 回读结果。中间任何一步
超时、进程重启，调用方都无法确定消息到底发出去没有。只靠内存 `seen` 或 "发送成功
才记 seen" 会在重启后重复发送。

## 状态机

    reserved  —— 已占位，正在发送（含超时/崩溃后留下的孤儿）
    sent      —— 发送成功（终态：永不重发）
    failed    —— 发送失败或结果未知（默认不自动重试，避免重复；需要人工确认后重置）

`reserve()` 的返回：

- `reserved`：拿到发送权；
- `duplicate`：已经 `sent`，跳过；
- `in_flight`：另一个进程/协程正在发（租约内）；
- `stale_reserved`：上次 `reserved` 超过租约，结果未知，**不自动重发**，转 `failed`；
- `failed`：以前失败过，**不自动重发**（崩溃/超时后重试可能重复）。

设计上宁可漏一条（人工重发），也不重复刷屏。
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    conversation_id TEXT NOT NULL,
    message_id      TEXT NOT NULL,
    state           TEXT NOT NULL,
    text_hash       TEXT NOT NULL DEFAULT '',
    reply_hash      TEXT NOT NULL DEFAULT '',
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    error           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (conversation_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_sends_state ON sends(state, updated_at);
"""


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:32]


class SendOutbox:
    """一个进程内使用的轻量 SQLite 幂等箱（WAL，线程/协程安全靠串行化事务）。"""

    def __init__(self, path: str | Path, *, lease_seconds: float = 300.0):
        raw = str(path or ":memory:")
        self.path = raw
        if raw != ":memory:":
            Path(raw).parent.mkdir(parents=True, exist_ok=True)
        self.lease_seconds = float(lease_seconds or 300)
        self._db = sqlite3.connect(raw, timeout=10, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:                        # noqa: BLE001
            pass

    def _row(self, conv_id: str, message_id: str) -> dict[str, Any] | None:
        cur = self._db.execute(
            "SELECT conversation_id, message_id, state, attempts, created_at, "
            "updated_at, error FROM sends WHERE conversation_id=? AND message_id=?",
            (str(conv_id), str(message_id)))
        row = cur.fetchone()
        if not row:
            return None
        return {"conversation_id": row[0], "message_id": row[1], "state": row[2],
                "attempts": row[3], "created_at": row[4], "updated_at": row[5],
                "error": row[6]}

    def get(self, conv_id: str, message_id: str) -> dict[str, Any] | None:
        return self._row(conv_id, message_id)

    def reserve(self, conv_id: str, message_id: str, *, text: str = "",
                reply: str = "") -> dict[str, Any]:
        """原子占位；只有返回 `action == "reserved"` 才允许发送。"""
        conv_id, message_id = str(conv_id or ""), str(message_id or "")
        if not conv_id or not message_id:
            return {"action": "invalid", "send": False,
                    "error": "conversation_id/message_id 不能为空"}
        now = time.time()
        try:
            self._db.execute("BEGIN IMMEDIATE")
            row = self._row(conv_id, message_id)
            if row is None:
                self._db.execute(
                    "INSERT INTO sends(conversation_id, message_id, state, text_hash, "
                    "reply_hash, attempts, created_at, updated_at, error) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (conv_id, message_id, "reserved", _hash(text), _hash(reply),
                     1, now, now, ""))
                self._db.commit()
                return {"action": "reserved", "send": True,
                        "record": self._row(conv_id, message_id)}
            state = str(row.get("state") or "")
            if state == "sent":
                self._db.commit()
                return {"action": "duplicate", "send": False, "record": row,
                        "error": "这条消息已经回复过（幂等箱 sent）"}
            if state == "reserved":
                age = now - float(row.get("updated_at") or 0)
                if age <= self.lease_seconds:
                    self._db.commit()
                    return {"action": "in_flight", "send": False, "record": row,
                            "error": "同一条消息正在发送（幂等箱 in_flight）"}
                # 上次占位后崩溃/超时：结果未知，转为 failed，绝不自动重发
                self._db.execute(
                    "UPDATE sends SET state='failed', updated_at=?, error=? "
                    "WHERE conversation_id=? AND message_id=?",
                    (now, "stale reserved: previous send result unknown",
                     conv_id, message_id))
                self._db.commit()
                row = self._row(conv_id, message_id) or row
                return {"action": "stale_reserved", "send": False, "record": row,
                        "error": "上次发送结果未知（占位超时），为避免重复不自动重发；请人工确认"}
            # failed：默认不自动重发（可能是"点了发送但没收到响应"）
            self._db.commit()
            return {"action": "failed", "send": False, "record": row,
                    "error": "上次发送失败/结果未知，为避免重复不自动重发；请人工确认"}
        except Exception as exc:                 # noqa: BLE001
            try:
                self._db.rollback()
            except Exception:                    # noqa: BLE001
                pass
            return {"action": "error", "send": False, "error": str(exc)[:200]}

    def mark_sent(self, conv_id: str, message_id: str) -> None:
        now = time.time()
        with self._db:
            self._db.execute(
                "UPDATE sends SET state='sent', updated_at=?, error='' "
                "WHERE conversation_id=? AND message_id=?",
                (now, str(conv_id), str(message_id)))

    def mark_failed(self, conv_id: str, message_id: str, error: str = "",
                    *, unknown: bool = False) -> None:
        now = time.time()
        msg = str(error or "")[:400]
        if unknown:
            msg = ("result unknown: " + msg).strip()
        with self._db:
            self._db.execute(
                "UPDATE sends SET state='failed', updated_at=?, error=?, "
                "attempts=attempts+1 WHERE conversation_id=? AND message_id=?",
                (now, msg, str(conv_id), str(message_id)))

    def stats(self) -> dict[str, Any]:
        out: dict[str, int] = {}
        for state, n in self._db.execute(
                "SELECT state, COUNT(*) FROM sends GROUP BY state"):
            out[str(state)] = int(n)
        return {"total": sum(out.values()), **out, "path": self.path}
