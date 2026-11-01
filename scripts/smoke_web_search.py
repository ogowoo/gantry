# -*- coding: utf-8 -*-
"""联网验证：重启后的服务里，web_search 对"今天 AI 新闻"这类 query 必须有结果。"""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765/v1/plugins/web-search/api/search?"
QUERIES = ("今天 AI 新闻", "small language model news", "2026 小模型 发布")

bad = 0
for q in QUERIES:
    url = BASE + urllib.parse.urlencode({"q": q, "n": 3})
    try:
        d = json.loads(urllib.request.urlopen(url, timeout=120).read())
        ok = (d.get("count") or 0) > 0
        bad += 0 if ok else 1
        print(f"[{q}] backend={d.get('backend')} count={d.get('count')} "
              f"errors={d.get('errors')}")
        for it in (d.get("results") or [])[:2]:
            print("   -", str(it.get("title"))[:70], "|", str(it.get("url"))[:45])
    except Exception as exc:                         # noqa: BLE001
        bad += 1
        print(f"[{q}] 失败 {type(exc).__name__} {str(exc)[:100]}")

print("结论:", "全部有结果" if bad == 0 else f"{bad} 个 query 没结果")
