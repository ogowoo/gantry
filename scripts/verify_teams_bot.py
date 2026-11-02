# -*- coding: utf-8 -*-
"""一次跑完：等版本更新 → 开 dry_run 跑一轮 → probe 看作者名 → 复位关闭。

不再重启服务（用户要求：这台机器重新认证很慢）。
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, timeout=600):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def post(path, body=None, timeout=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


print("=== 1) 等 preview.45")
for _ in range(30):
    try:
        h = get("/health", 5)
        if "preview.45" in str(h.get("version")):
            print("version:", h["version"])
            break
    except Exception:                            # noqa: BLE001
        pass
    time.sleep(2)

print("\n=== 2) 设成 enabled=true + dry_run=true，跑一轮（不发送）")
print(post("/v1/plugins/teams-bot/api/settings",
           {"enabled": True, "dry_run": True, "chats": [],
            "mention_tokens": ["@Gantry"], "max_replies_per_cycle": 1})["status"]["enabled"])
try:
    run = post("/v1/plugins/teams-bot/api/run", timeout=900)
    print("周期结果:", json.dumps({k: run.get(k) for k in
                                  ("at", "replied", "chats", "errors")},
                                 ensure_ascii=False)[:600])
    print("would:", json.dumps(run.get("would") or [], ensure_ascii=False)[:600])
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read().decode()[:400])

print("\n=== 3) probe：作者名是否配上了")
try:
    d = get("/v1/plugins/teams-bot/api/probe")
    msgs = d.get("messages") or []
    print(f"消息 {len(msgs)} 条，示例:")
    for m in msgs[-4:]:
        print("   ", json.dumps({k: m.get(k) for k in ("author", "mine", "text")},
                                ensure_ascii=False)[:180])
    print("composer:", d.get("composer"), "| chats:", len(d.get("chats") or []))
except urllib.error.HTTPError as exc:
    print("HTTP", exc.code, exc.read().decode()[:300])

print("\n=== 4) 复位：enabled=false, dry_run=true")
st = post("/v1/plugins/teams-bot/api/settings", {"enabled": False, "dry_run": True})["status"]
print("enabled:", st["enabled"], "| dry_run:", st["dry_run"])
print("\n=== 5) 最终状态")
s = get("/v1/plugins/teams-bot/api/status")
print(json.dumps({k: s.get(k) for k in ("enabled", "dry_run", "seen", "replies",
                                        "last_cycle_at", "last_error")},
                 ensure_ascii=False))
print("kb:", json.dumps(s.get("kb"), ensure_ascii=False))
