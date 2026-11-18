# -*- coding: utf-8 -*-
"""热重载后去 Lynn Ling 会话里看：能否滚动找到、是否是 1:1、作者归属对不对。

全程 dry_run（触发词设成不会出现的东西），不发任何消息。
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, timeout=900):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def post(path, body=None, timeout=1200):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


print("=== 1) 热重载（不重启服务）")
r = post("/v1/plugins/teams-bot/reload")
print("status:", r["plugin"]["status"], "| error:", r["plugin"].get("error"))

st = get("/v1/plugins/teams-bot/api/status")
print("interval:", st["interval_seconds"], "| only_direct:", st.get("only_direct"),
      "| dry_run:", st["dry_run"], "| enabled:", st["enabled"])

print("\n=== 2) 打开 Lynn Ling（dry_run，绝不发送）")
post("/v1/plugins/teams-bot/api/settings", {
    "enabled": True, "dry_run": True, "chats": ["Lynn Ling"],
    "mention_tokens": ["@__never_match__"], "fallback_agent": False,
    "only_direct": True})
run = post("/v1/plugins/teams-bot/api/run")
print("replied:", run.get("replied"), "| chats:", json.dumps(run.get("chats"), ensure_ascii=False))
print("errors:", json.dumps(run.get("errors"), ensure_ascii=False)[:300])

print("\n=== 3) probe：类型 + 作者")
d = get("/v1/plugins/teams-bot/api/probe")
print("title:", (d.get("title") or "")[:80])
print("participants:", d.get("participants"), "| kind:", d.get("kind"),
      "| signed_in:", repr(d.get("signed_in")))
for m in (d.get("messages") or []):
    print(f"   [{m.get('author') or '?'}] mine={m.get('mine')} :: {(m.get('text') or '')[:70]}")

print("\n=== 4) 复位")
post("/v1/plugins/teams-bot/api/settings", {
    "enabled": False, "dry_run": True, "chats": [], "mention_tokens": ["@Gantry"],
    "fallback_agent": True})
print("已关闭并复位")
