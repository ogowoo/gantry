# -*- coding: utf-8 -*-
"""解析 DSH 会话 JSONL：找出"本地知识库 / 插件"相关的对话片段。"""
import json
from pathlib import Path

p = Path("data/tmp-session.jsonl")
lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
print("行数:", len(lines))

# 先看一条的结构
for ln in lines:
    try:
        rec = json.loads(ln)
    except Exception:                              # noqa: BLE001
        continue
    print("记录键:", sorted(rec.keys())[:14])
    break

def text_of(rec: dict) -> tuple[str, str]:
    role = str(rec.get("role") or rec.get("type") or "")
    content = rec.get("content")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text") or ""))
            else:
                parts.append(str(c))
        content = "\n".join(parts)
    elif not isinstance(content, str):
        content = ""
    if not content:
        msg = rec.get("message")
        if isinstance(msg, dict):
            role = str(msg.get("role") or role)
            c2 = msg.get("content")
            content = c2 if isinstance(c2, str) else ""
    if not role:
        payload = rec.get("payload") or {}
        if isinstance(payload, dict):
            role = str(payload.get("role") or payload.get("type") or "")
            c3 = payload.get("content") or payload.get("text") or ""
            content = c3 if isinstance(c3, str) else ""
    return role, content

msgs = []
for ln in lines:
    try:
        rec = json.loads(ln)
    except Exception:                              # noqa: BLE001
        continue
    role, content = text_of(rec)
    if content.strip() and role in ("user", "assistant"):
        msgs.append((role, content))

print("可用消息:", len(msgs))
if not msgs:
    # 兜底：把所有字符串里的 "知识库" 附近内容打出来
    hits = 0
    for i, ln in enumerate(lines):
        if "知识库" in ln:
            hits += 1
            print(f"\n--- line {i} ---")
            print(ln[:600])
            if hits >= 8:
                break
    raise SystemExit(0)

KEY = ("知识库", "kb_", "kb.py", "KB", "插件", "plugin")
picked = [(r, t) for r, t in msgs if any(k in t for k in KEY)]
print("相关消息:", len(picked))
for role, text in picked[-10:]:
    print(f"\n===== [{role}] =====")
    print(text.replace("\r", "")[:1200])
