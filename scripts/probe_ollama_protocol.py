# -*- coding: utf-8 -*-
"""看这个 4B 模型在"Agent 协议"下的原始输出长什么样（判断是模型问题还是解析问题）。"""
import json
import time
import urllib.request

M = "fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:4b"
SYS = ("你是本地任务 Agent。每次回复**只输出一个 JSON 对象**，格式："
       '{"type":"tool","tool":"工具名","arguments":{...},"reason":"为什么"} '
       '或 {"type":"final","content":"给用户的答复"}。'
       "可用工具：list_directory(path)、read_text(path)、find_files(pattern)。")
USER = "用 list_directory 看一下当前工作区有哪些文件，然后用 final 汇报。"

body = {"model": M, "temperature": 0, "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": USER}]}
req = urllib.request.Request("http://127.0.0.1:11434/v1/chat/completions",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json",
                                      "Authorization": "Bearer ollama"})
t0 = time.time()
d = json.loads(urllib.request.urlopen(req, timeout=600).read())
el = time.time() - t0
msg = d["choices"][0]["message"]
print(f"耗时 {el:.0f}s | finish={d['choices'][0].get('finish_reason')}")
print("content:", repr(str(msg.get("content"))[:400]))
print("reasoning 长度:", len(str(msg.get("reasoning") or "")))
