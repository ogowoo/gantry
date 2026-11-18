# -*- coding: utf-8 -*-
"""P1 端到端验证（只发 Chen, Alex 自己会话）：触发消息 → cycle 入队 → worker 真发送。

用法：python scripts/test_p1_live.py [--peer "Chen, Alex"] [--keep]
默认跑完会复位所有开关（enabled=false / dry_run=true / send_enabled=false / chats=[]）。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path: str, body: dict, timeout: float = 1200) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def get(path: str, timeout: float = 900) -> dict:
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--peer", default="Chen, Alex")
    ap.add_argument("--keep", action="store_true", help="跑完不复位（调试用）")
    args = ap.parse_args()
    peer = args.peer

    watch = get("/v1/plugins/teams-bot/api/idb-watch")
    row = next((x for x in (watch.get("list") or [])
                if str(x.get("peerName") or "").lower() == peer.lower()), None)
    if not row:
        print(f"✗ 找不到会话 {peer!r}（先确认 Teams 已登录）")
        return 2
    cid = row["id"]
    print(f"target: {peer!r} | total={watch.get('total')} direct={watch.get('direct')}")

    print("\n== 0) 刷新页面，读当前会话最后一条")
    post("/v1/plugins/teams-bot/api/reload-teams", {})
    d = get("/v1/plugins/teams-bot/api/probe")
    print("   ", d.get("title"), "|", str((d.get("messages") or [{}])[-1].get("text"))[:60])

    print("\n== 1) 发一条 @Gantry 触发消息（force 绕过 dry_run）")
    r = post("/v1/plugins/teams-bot/api/reply", {
        "peer": peer, "text": "@Gantry P1 队列测试，收到请忽略",
        "send": True, "force": True, "conv_id": cid, "strict": False})
    print("   ->", {k: r.get(k) for k in ("sent", "how", "via", "error")})
    time.sleep(2)
    d = get("/v1/plugins/teams-bot/api/probe")
    print("   最后一条:", str((d.get("messages") or [{}])[-1].get("text"))[:70])

    print("\n== 2) chats 白名单 + 真发，跑一轮 cycle")
    post("/v1/plugins/teams-bot/api/settings", {
        "enabled": True, "dry_run": False, "send_enabled": True,
        "chats": [peer], "only_unread": False, "only_direct": True,
        "my_name": "", "fallback_agent": False, "reply_when_unknown": True,
        "max_replies_per_cycle": 1})
    out = post("/v1/plugins/teams-bot/api/run", {})
    print("   cycle:", {k: out.get(k) for k in ("scanned", "filtered", "candidates",
                                                "queued", "handled")})

    print("\n== 3) 等 worker 发完")
    st = {}
    for _ in range(40):
        time.sleep(2)
        st = get("/v1/plugins/teams-bot/api/status", 120)
        if not st.get("queue_size") and not st.get("sending"):
            break
    print("   queue_size:", st.get("queue_size"), "| replies:", st.get("replies"))

    d = get("/v1/plugins/teams-bot/api/probe")
    print("\n== 4) 会话最后两条")
    for m in (d.get("messages") or [])[-2:]:
        print("   ", m.get("author"), "|", str(m.get("text"))[:90])

    if not args.keep:
        post("/v1/plugins/teams-bot/api/settings", {
            "enabled": False, "dry_run": True, "send_enabled": False, "chats": [],
            "only_unread": True, "fallback_agent": True, "my_name": ""})
        print("\n已复位：enabled=false / dry_run=true / send_enabled=false / chats=[]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
