# -*- coding: utf-8 -*-
"""端到端 dry_run 验证：给一个真实会话，把 mention_tokens 临时设成一个真实出现过的词，
看它是否读到消息、命中触发、产出 would[]（不发送、不走 Copilot）。最后复位。"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path, body=None, timeout=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


print("=== 设成：盯索引 0 的会话，触发词用真实出现过的 'thank you'，不走模型")
st = post("/v1/plugins/teams-bot/api/settings", {
    "enabled": True, "dry_run": True, "chats": ["0"],
    "mention_tokens": ["thank you"], "fallback_agent": False,
    "reply_when_unknown": True, "max_replies_per_cycle": 1})["status"]
print("enabled:", st["enabled"], "| chats:", st["chats"], "| tokens:", st["mention_tokens"])

print("\n=== 跑一轮")
run = post("/v1/plugins/teams-bot/api/run")
print("replied:", run.get("replied"), "| chats:", json.dumps(run.get("chats"), ensure_ascii=False)[:300])
print("errors:", json.dumps(run.get("errors"), ensure_ascii=False)[:300])
print("would:")
for w in run.get("would") or []:
    print("   chat:", w.get("chat"), "| source:", w.get("source"))
    print("   question:", w.get("question"))
    print("   text:", (w.get("text") or "")[:200])

print("\n=== 复位（关闭 + 恢复 @Gantry）")
st = post("/v1/plugins/teams-bot/api/settings", {
    "enabled": False, "dry_run": True, "chats": [],
    "mention_tokens": ["@Gantry"], "fallback_agent": True})["status"]
print("enabled:", st["enabled"], "| dry_run:", st["dry_run"], "| tokens:", st["mention_tokens"])
