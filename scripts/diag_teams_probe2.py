# -*- coding: utf-8 -*-
"""完整打印 probe 结果（不重启服务，浏览器是热的）：tids 全量、列表容器、样本项、输入框、消息。"""
import json
import time
import urllib.error
import urllib.request

try:
    d = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8765/v1/plugins/teams-bot/api/probe", timeout=600).read())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800])
    raise SystemExit(1)

print("url:", d.get("url"))
print("title:", d.get("title"))
print("composer:", d.get("composer"))
dom = d.get("dom") or {}
tids = dom.get("tids") or {}
print("\n=== 所有 data-tid")
for k, v in sorted(tids.items(), key=lambda kv: -kv[1]):
    print(f"  {v:5d}  {k}")
print("\n=== 列表容器:", json.dumps(dom.get("listish"), ensure_ascii=False))
print("\n=== 列表项样本:", json.dumps(dom.get("sample"), ensure_ascii=False, indent=1))
print("\n=== 输入框:", json.dumps(dom.get("editable"), ensure_ascii=False))
print("\n=== 选中的会话项（chats 前 3）:", json.dumps((d.get("chats") or [])[:3],
                                                   ensure_ascii=False, indent=1))
print("\n=== 最近消息（全部）:")
for m in (d.get("messages") or []):
    print("   ", json.dumps(m, ensure_ascii=False))
print("\n=== composer 命中:", json.dumps((d.get("counts") or {}).get("composer"),
                                       ensure_ascii=False))
print("=== chat_item 命中:", json.dumps((d.get("counts") or {}).get("chat_item"),
                                       ensure_ascii=False))
print("=== message 命中:", json.dumps((d.get("counts") or {}).get("message"),
                                     ensure_ascii=False))
print("=== text 命中:", json.dumps((d.get("counts") or {}).get("text"), ensure_ascii=False))
