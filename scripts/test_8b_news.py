# -*- coding: utf-8 -*-
"""复现用户那一问（8B + Agent 模式），看修好搜索工具后能不能搜到真东西。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
Q = "搜索一下新闻，看看今天为止都有哪些出色的小模型"


def post(path, body, timeout=1200):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


for model in ("fflm/deepseek-r1-0528:8b",):
    print(f"=== Agent 模式 {model}")
    t0 = time.time()
    d = post("/v1/agent/completions", {"model": model, "stream": False, "agent_mode": True,
                                      "messages": [{"role": "user", "content": Q}]})
    el = time.time() - t0
    final = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    steps = d.get("agent_steps") or []
    print(f"{el:.0f}s | 会话 {d.get('session_id')} | 步数 {len(steps)}")
    for s in steps[:8]:
        print(f"  #{s.get('step')} {s.get('status')} {s.get('tool') or '-'} "
              f"{str(s.get('reason') or s.get('error') or '')[:70]}".replace("\n", " "))
    print("--- final ---")
    print(final.strip()[:900])
