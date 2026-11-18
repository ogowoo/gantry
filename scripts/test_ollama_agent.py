# -*- coding: utf-8 -*-
"""真跑一遍 Ollama 上的 Agent 任务，并把每一步动作/耗时打出来。

这个 4B 模型是**思考型**（先吐 reasoning 再给正文，单轮 20-60 秒），所以：
  · 给足 max_tokens（provider 配了 4096）；
  · 超时放长（服务端 provider timeout 600s、客户端 900s）；
  · 只让它做一件小事（列文件），看它能不能按协议吐 JSON 动作。
"""
import json
import sys
import time
import urllib.request

MODEL = "ollama/fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:4b"
TASK = "用 list_directory 看一下当前工作区有哪些文件，然后用 final 汇报文件名列表。"

body = {"model": MODEL, "stream": False, "agent_mode": True,
        "messages": [{"role": "user", "content": TASK}]}
req = urllib.request.Request("http://127.0.0.1:8765/v1/agent/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
t0 = time.time()
try:
    raw = urllib.request.urlopen(req, timeout=900).read()
except Exception as exc:                        # noqa: BLE001
    print("请求失败:", type(exc).__name__, exc)
    sys.exit(1)
el = time.time() - t0
data = json.loads(raw)
print(f"总耗时 {el:.0f}s | 会话 {data.get('session_id')} | 工作区 {data.get('workspace')}")
content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
print("最终回答:", content.strip()[:300])
steps = data.get("agent_steps") or []
print(f"步骤 {len(steps)} 条：")
for s in steps[:12]:
    print(f"  #{s.get('step')} {s.get('status')} {s.get('tool') or ''} "
          f"{(s.get('reason') or '')[:50]}")
    if s.get("error"):
        print("      错误:", str(s["error"])[:160].replace("\n", " "))
