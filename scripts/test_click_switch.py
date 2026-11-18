# -*- coding: utf-8 -*-
"""验证点击位置修复：按索引打开不同会话，看头部标题是否真的变化（只读，不发送）。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, t=900):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=t).read())


def post(path, body=None, t=1200):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


for idx in ("0", "3"):
    post("/v1/plugins/teams-bot/api/settings",
         {"enabled": True, "dry_run": True, "chats": [idx],
          "mention_tokens": ["@__never__"], "fallback_agent": False, "only_direct": False})
    run = post("/v1/plugins/teams-bot/api/run", t=1800)
    info = (run.get("chats") or [{}])[0]
    d = get("/v1/plugins/teams-bot/api/probe", 900)
    print(f"index {idx}: opened={info.get('opened')} | title={(d.get('title') or '')[:70]}")
    print(f"   participants={d.get('participants')} kind={d.get('kind')}")
    print(f"   errors={json.dumps(run.get('errors'), ensure_ascii=False)[:160]}")

post("/v1/plugins/teams-bot/api/settings",
     {"enabled": False, "dry_run": True, "chats": [], "mention_tokens": ["@Gantry"],
      "fallback_agent": True})
print("已复位（enabled=false / dry_run=true）")
