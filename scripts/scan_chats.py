# -*- coding: utf-8 -*-
"""逐个打开会话，报出每个的参与人数/类型/首条消息作者，找出真正的 1:1。

为什么这么干：会话项的 innerText = "名字 + 最后一条消息预览"，按文本匹配会命中
"别人在群里提到 Lynn Ling"的群聊（实测就把 AI GROUP 当成了 Lynn Ling）。
按索引打开 + 读 participants 才可靠。
全程 dry_run、不发送；探测完复位。
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path, timeout=900):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=timeout).read())


def post(path, body=None, timeout=1800):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


post("/v1/plugins/teams-bot/api/settings",
     {"enabled": False, "dry_run": True})     # 保证轮询不会插进来

# 先把 teams 模块暴露给脚本用（通过插件的 probe 拿不到"按索引打开"）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

chats = get("/v1/plugins/teams-bot/api/status")
print("chats 配置:", chats.get("chats"))

# 用插件自带的工具面拿不到这些，只能靠 probe 里的列表 + 逐个 index 打开：
# 走 API：settings.chats 设为索引，跑一轮，看 chats[].skipped/kind
for idx in range(0, 10):
    post("/v1/plugins/teams-bot/api/settings",
         {"enabled": True, "dry_run": True, "chats": [str(idx)],
          "mention_tokens": ["@__never_match__"], "fallback_agent": False,
          "only_direct": False})
    try:
        run = post("/v1/plugins/teams-bot/api/run", timeout=1800)
        info = (run.get("chats") or [{}])[0]
        print(f"index {idx}: opened={info.get('opened')} skipped={info.get('skipped')!r} "
              f"candidates={info.get('candidates')} errors={len(run.get('errors') or [])}")
        if run.get("errors"):
            print("    err:", str(run["errors"][0])[:120])
        d = get("/v1/plugins/teams-bot/api/probe", timeout=900)
        print(f"    title={ (d.get('title') or '')[:60]!r} participants={d.get('participants')} "
              f"kind={d.get('kind')}")
        msgs = d.get("messages") or []
        for m in msgs[-2:]:
            print(f"      [{m.get('author') or '?'}] {(m.get('text') or '')[:50]}")
    except Exception as exc:                     # noqa: BLE001
        print(f"index {idx}: 失败 {type(exc).__name__} {str(exc)[:120]}")

post("/v1/plugins/teams-bot/api/settings",
     {"enabled": False, "dry_run": True, "chats": [], "mention_tokens": ["@Gantry"],
      "fallback_agent": True})
print("已复位")
