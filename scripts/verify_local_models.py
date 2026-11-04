# -*- coding: utf-8 -*-
"""验证 preview.51：内嵌模型（local/minicpm5-2b）可用，且解耦模式（外部 HTTP）不受影响。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, t=300):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=t).read())


def post(path, body, t=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


print("== /v1/models")
ms = get("/v1/models")
ids = [m.get("id") for m in (ms.get("data") or [])]
print("  local 模型:", [i for i in ids if str(i).startswith("local/")])
print("  外部模型仍可用:", [i for i in ids if str(i).startswith(("llamacpp/", "ollama/", "fflm/"))][:4])

print("\n== 进程内聊天（非流式）")
t0 = time.time()
r = post("/v1/chat/completions", {
    "model": "local/minicpm5-2b",
    "messages": [{"role": "user", "content": "用一句话解释什么是防火墙。"}],
    "max_tokens": 200, "stream": False})
msg = (r.get("choices") or [{}])[0].get("message") or {}
content = str(msg.get("content") or "")
print(f"  耗时 {time.time() - t0:.1f}s | 长度 {len(content)} | 含 <think>:", "<think" in content)
print("  内容:", content[:110].replace("\n", " "))
print("  usage:", r.get("usage"))

print("\n== 进程内聊天（流式）")
req = urllib.request.Request(
    BASE + "/v1/chat/completions",
    data=json.dumps({"model": "local/minicpm5-2b", "stream": True,
                     "messages": [{"role": "user", "content": "数到三，每个数字一行。"}],
                     "max_tokens": 80}).encode(),
    headers={"Content-Type": "application/json"})
text, chunks = "", 0
with urllib.request.urlopen(req, timeout=600) as resp:
    for line in resp:
        line = line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if raw == "[DONE]":
            break
        try:
            ev = json.loads(raw)
        except Exception:                        # noqa: BLE001
            continue
        delta = ((ev.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""
        if delta:
            text += delta
            chunks += 1
print(f"  chunk 数 {chunks} | 含 <think>:", "<think" in text)
print("  内容:", text[:120].replace("\n", " "))

print("\n== 知识库 embedding 走进程内")
print("  vector-stats:", json.dumps(get("/v1/plugins/kb/api/vector-stats"), ensure_ascii=False)[:260])
d = post("/v1/plugins/kb/api/embed", {"force": False})
print("  embed:", json.dumps({k: d.get(k) for k in ("ok", "model", "vectorised", "chunks", "added")},
                             ensure_ascii=False))

print("\n== 解耦检查：外部 provider 仍然直连 HTTP")
cfg = get("/v1/config")
provs = (cfg.get("providers") or {})
for name in ("llamacpp", "local", "ollama", "fflm"):
    if name in provs:
        p = provs[name]
        print(f"  {name}: backend={p.get('backend') or 'http'} "
              f"base_url={str(p.get('base_url') or '')[:40]}")
