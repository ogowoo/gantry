# -*- coding: utf-8 -*-
"""验证 preview.50：kb 插件加载 + 向量工具/接口在线可用。"""
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, t=300):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=t).read())


def post(path, body=None, t=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


h = {}
for _ in range(40):
    try:
        h = get("/health", 5)
        if "preview.50" in str(h.get("version")):
            break
    except Exception:                              # noqa: BLE001
        pass
    time.sleep(3)
print("version:", h.get("version"))

pl = get("/v1/plugins", 60)
kb = [p for p in pl.get("plugins") or [] if p.get("name") == "kb"]
print("kb 插件:", kb[0].get("status") if kb else "缺失",
      "| tools:", (kb[0].get("contributes") or {}).get("tools") if kb else None)

tl = get("/v1/agent/tools", 90)
names = [t.get("name") or (t.get("function") or {}).get("name") for t in (tl.get("tools") or [])]
print("工具面 kb_*:", [n for n in names if n and n.startswith("kb_")])

print("\n== vector-stats")
print("  ", json.dumps(get("/v1/plugins/kb/api/vector-stats", 120), ensure_ascii=False))

print("\n== api/embed（确保向量就绪）")
print("  ", json.dumps({k: v for k, v in post("/v1/plugins/kb/api/embed", {"force": False}).items()
                        if k in ("ok", "vectorised", "chunks", "dim", "added", "model")},
                       ensure_ascii=False))

print("\n== api/search（默认 auto → hybrid）")
q = urllib.parse.urlencode({"q": "公司网络访问不了外部网站", "n": 3})
d = get("/v1/plugins/kb/api/search?" + q, 180)
print("  backend:", d.get("backend"), "| count:", d.get("count"))
for hit in d.get("hits") or []:
    print(f"   {hit.get('score'):.3f} {hit.get('cite')}")

print("\n== api/semantic（纯向量）")
d = get("/v1/plugins/kb/api/semantic?" + q, 180)
print("  count:", d.get("count"))
for hit in d.get("hits") or []:
    print(f"   {hit.get('score'):.3f} {hit.get('cite')}")
