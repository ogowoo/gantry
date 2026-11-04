# -*- coding: utf-8 -*-
"""复现并验证修复：向 local/minicpm5-2b 发一个约 1 万 token 的提示。

修复前：llama.cpp 直接报 "Requested tokens (10192) exceed context window of 4096"。
修复后：n_ctx=16384 直接装下；即使超了也会自动裁剪（保 system + 最近消息）或给出可行动错误。
"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path, body, t=900):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


for _ in range(40):                              # 等服务起来
    try:
        h = json.loads(urllib.request.urlopen(BASE + "/health", timeout=5).read())
        if "preview.52" in str(h.get("version")) or "preview.51" in str(h.get("version")):
            break
    except Exception:                            # noqa: BLE001
        pass
    time.sleep(3)
print("version:", h.get("version"))

long_text = "这是一段很长的背景资料，用来模拟 Gantry 的系统提示与工具清单。" * 500
print("提示字符数:", len(long_text), "（约 1 万 token 量级）")

t0 = time.time()
try:
    r = post("/v1/chat/completions", {
        "model": "local/minicpm5-2b",
        "messages": [{"role": "system", "content": "你是 IT 支持助手。"},
                     {"role": "user", "content": long_text + "\n\n请用一句话总结上文说了什么。"}],
        "max_tokens": 120, "stream": False})
    el = time.time() - t0
    msg = (r.get("choices") or [{}])[0].get("message") or {}
    content = str(msg.get("content") or "")
    print(f"✓ 成功（{el:.1f}s，{len(content)} 字，含 <think>: {'<think' in content}）")
    print("  内容:", content[:100].replace("\n", " "))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", "replace")
    print("✗ HTTP", exc.code, body[:300])
