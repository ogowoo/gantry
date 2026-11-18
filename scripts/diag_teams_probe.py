# -*- coding: utf-8 -*-
"""跑 probe 并把 DOM 摘要打印出来（定位会话列表/消息/输入框的真实结构）。"""
import json
import time
import urllib.error
import urllib.request

t0 = time.time()
try:
    r = urllib.request.urlopen("http://127.0.0.1:8765/v1/plugins/teams-bot/api/probe", timeout=400)
    d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800])
    raise SystemExit(1)
except Exception as e:                              # noqa: BLE001
    print("失败", type(e).__name__, str(e)[:300])
    raise SystemExit(1)

print(f"耗时 {time.time() - t0:.0f}s | url={d.get('url')} | title={d.get('title')!r}")
print("composer:", d.get("composer"), "| chats:", len(d.get("chats") or []),
      "| messages:", len(d.get("messages") or []))
dom = d.get("dom") or {}
if dom.get("error"):
    print("dom 统计失败:", dom["error"])
else:
    tids = dom.get("tids") or {}
    print("\n=== data-tid（按次数，前 25）")
    for k, v in sorted(tids.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {v:5d}  {k}")
    print("\n=== role")
    for k, v in sorted((dom.get("roles") or {}).items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {v:5d}  {k}")
    print("\n=== 像列表的容器")
    for it in dom.get("listish") or []:
        print("  ", json.dumps(it, ensure_ascii=False))
    print("\n=== 第一个列表项样本:", json.dumps(dom.get("sample"), ensure_ascii=False))
    print("=== 输入框:", json.dumps(dom.get("editable"), ensure_ascii=False))
    print("=== iframe:", json.dumps(dom.get("iframes") or [], ensure_ascii=False)[:400])
print("\nhtml:", d.get("html"))
