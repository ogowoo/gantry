# -*- coding: utf-8 -*-
"""读 DSH 会话记录（zstd 压缩的 jsonl），把和"本地知识库"相关的对话摘要出来。

会话文件：~/.dsh/sessions/<workspace>/<session-id>/session.v4.jsonl.zstd
只要文本消息（user/assistant），按时间顺序，过滤含关键词的片段。
"""
import io
import json
import sys
from pathlib import Path

try:
    import zstandard as zstd
except Exception:                                  # noqa: BLE001
    zstd = None

SID = "session-3b59fe96-da9b-4c50-83e5-c85b88bf7caa"
root = Path.home() / ".dsh" / "sessions"
cand = list(root.rglob(f"{SID}/session.v4.jsonl.zstd"))
if not cand:
    print("找不到会话文件")
    sys.exit(1)
path = cand[0]
print("文件:", path)

raw = path.read_bytes()
if zstd is not None:
    text = zstd.ZstdDecompressor().stream_reader(io.BytesIO(raw)).read().decode("utf-8", "replace")
else:
    import subprocess

    text = subprocess.run(["zstd", "-dc", str(path)], capture_output=True).stdout.decode("utf-8", "replace")

lines = [ln for ln in text.splitlines() if ln.strip()]
print("记录行数:", len(lines))

msgs = []
for ln in lines:
    try:
        rec = json.loads(ln)
    except Exception:                              # noqa: BLE001
        continue
    role = rec.get("role") or rec.get("type") or ""
    content = rec.get("content") or rec.get("text") or ""
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text") or c.get("content") or ""))
            else:
                parts.append(str(c))
        content = " ".join(parts)
    content = str(content)
    if not content.strip():
        continue
    if role not in ("user", "assistant"):
        # 有些记录把消息放在 message 字段里
        m = rec.get("message") or {}
        if isinstance(m, dict) and m.get("role"):
            role = m["role"]
            content = str(m.get("content") or "")
        else:
            continue
    msgs.append({"role": role, "text": content})

print("有效消息:", len(msgs))
KEY = ("知识库", "kb", "KB", "kb.py", "插件", "plugin")
hits = [m for m in msgs if any(k in m["text"] for k in KEY)]
print("含关键词消息:", len(hits))
for m in hits[-12:]:
    first = m["text"].replace("\n", " ")[:400]
    print(f"\n[{m['role']}] {first}")
