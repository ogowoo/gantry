# -*- coding: utf-8 -*-
"""FastFlowLM 是否支持 response_format（Gantry 的 JSON 模式依赖它）+ 模型清单。"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:52625/v1"
MODEL = "deepseek-r1-0528:8b"


def call(extra: dict, prompt: str) -> str:
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 512}
    body.update(extra)
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=300).read())
        msg = d["choices"][0]["message"]
        return f"ok {time.time() - t0:.0f}s content={str(msg.get('content'))[:90]!r}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read().decode()[:200]}"
    except Exception as exc:                       # noqa: BLE001
        return f"ERR {type(exc).__name__} {exc}"


print("json_object :", call({"response_format": {"type": "json_object"}},
                             "只输出 JSON：{\"ok\":true}"))
ids = json.loads(urllib.request.urlopen(BASE + "/models", timeout=15).read())
print("models     :", [m["id"] for m in ids.get("data", [])][:8])
