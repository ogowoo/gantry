# -*- coding: utf-8 -*-
"""经 Gantry 测 FastFlowLM 的 DeepSeek-R1 8B：普通对话 + 一次小 Agent 任务。"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
MODEL = "fflm/deepseek-r1-0528:8b"


def post(path: str, body: dict, timeout: int = 900) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


print("=== 1) 普通对话")
t0 = time.time()
d = post("/v1/chat/completions",
         {"model": MODEL, "stream": False,
          "messages": [{"role": "user", "content": "用一句话回答：1+1 等于几？"}]})
el = time.time() - t0
content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
print(f"耗时 {el:.0f}s | 正文: {content.strip()[:120]!r}")

print("\n=== 2) 小 Agent 任务（list_directory + final）")
t0 = time.time()
d2 = post("/v1/agent/completions", {
    "model": MODEL, "stream": False, "agent_mode": True,
    "messages": [{"role": "user", "content":
                  "列出当前工作区根目录的文件名（用 list_directory，path 用默认的 .），"
                  "然后只回一句 final 说明有几个文件。"}]})
el2 = time.time() - t0
final = ((d2.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
steps = d2.get("agent_steps") or []
print(f"耗时 {el2:.0f}s | 会话 {d2.get('session_id')} | 步数 {len(steps)}")
print("final:", final.strip()[:200])
for s in steps[:10]:
    print(f"  #{s.get('step')} {s.get('status')} {s.get('tool') or '-'} "
          f"{(str(s.get('reason') or s.get('error') or ''))[:70]}".replace("\n", " "))
