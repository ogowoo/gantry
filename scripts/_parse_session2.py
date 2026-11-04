# -*- coding: utf-8 -*-
"""正确解析该 DSH 会话：消息在 data.content / data.message.content 里。

只输出 user 与 assistant 的**正文**（跳过 reasoning / tool 调用），并按关键词定位
"本地知识库做成插件"的那段讨论。
"""
import json
from pathlib import Path

p = Path("data/tmp-session.jsonl")
recs = []
for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
    ln = ln.strip()
    if not ln:
        continue
    try:
        recs.append(json.loads(ln))
    except Exception:                              # noqa: BLE001
        continue


def texts(content) -> tuple[str, str]:
    """返回 (正文, reasoning)。"""
    body, think = [], []
    if isinstance(content, str):
        return content, ""
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                body.append(str(c))
                continue
            t = str(c.get("text") or "")
            if c.get("type") == "reasoning":
                think.append(t)
            elif c.get("type") in ("text", None):
                body.append(t)
    return "\n".join(x for x in body if x), "\n".join(think)


msgs = []
for rec in recs:
    typ = str(rec.get("type") or "")
    data = rec.get("data") or {}
    if typ == "user/message":
        body, _ = texts(data.get("content"))
        if body.strip():
            msgs.append(("user", body))
    elif typ == "assistant/message":
        msg = data.get("message") or {}
        body, think = texts(msg.get("content"))
        if body.strip() or think.strip():
            msgs.append(("assistant", body))

print("消息数:", len(msgs))
KEY = ("知识库", "kb_search", "kb.py", "RAG", "插件", "plugin")
picked = [(r, t) for r, t in msgs if any(k in t for k in KEY)]
print("相关消息:", len(picked))
for role, text in picked[-6:]:
    print(f"\n===== [{role}] =====")
    print(text[:2000])
