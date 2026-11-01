# -*- coding: utf-8 -*-
"""测 FastFlowLM（NPU 上的 OpenAI 兼容服务）：模型名、正文/思考字段、耗时。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:52625/v1"
MODELS = ["deepseek-r1-0528:8b", "deepseek-r1:8b"]


def chat(model: str, prompt: str, max_tokens: int = 2048, timeout: int = 300):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": max_tokens, "stream": False}
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    el = time.time() - t0
    ch = d["choices"][0]
    msg = ch.get("message") or {}
    return {"elapsed": round(el, 1), "finish": ch.get("finish_reason"),
            "content": str(msg.get("content") or ""),
            "reasoning": str(msg.get("reasoning") or msg.get("reasoning_content") or ""),
            "usage": d.get("usage")}


for model in MODELS:
    try:
        r = chat(model, "用一句话回答：1+1 等于几？")
        print(f"[{model}] {r['elapsed']}s finish={r['finish']} "
              f"usage={r['usage']}")
        print("   content:", repr(r["content"][:100]))
        print("   reasoning:", repr(r["reasoning"][:80]), "len=", len(r["reasoning"]))
    except Exception as exc:                       # noqa: BLE001
        print(f"[{model}] 失败: {type(exc).__name__} {exc}")
