# -*- coding: utf-8 -*-
"""线上验证 preview.56：① kb_add 工具注册；② engine=xiaomi 的 OCR 导入；③ /kb 与 📚 前端可用。"""
import base64
import io
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
BASE = "http://127.0.0.1:8765"


def get(p, t=600):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=t).read())


def post(p, body, t=900):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


h = {}
for _ in range(40):
    try:
        h = get("/health", 5)
        if "preview.56" in str(h.get("version")):
            break
    except Exception:                              # noqa: BLE001
        pass
    time.sleep(3)
print("version:", h.get("version"))

pl = get("/v1/plugins", 60)
kb = [p for p in pl.get("plugins") or [] if p.get("name") == "kb"][0]
print("kb 工具:", (kb.get("contributes") or {}).get("tools"))

# 切到 xiaomi 引擎（运行时生效：重新载入插件配置）
print("当前 OCR:", get("/v1/plugins/kb/api/stats").get("dir") and "ok")
req = urllib.request.Request(BASE + "/v1/plugins/kb/reload", data=b"{}",
                             headers={"Content-Type": "application/json"})
print("reload:", json.loads(urllib.request.urlopen(req, timeout=180).read())["plugin"]["status"])

# 造一张带表格的中文文档截图，用接口导入（此时 engine=xiaomi）
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

img = Image.new("RGB", (700, 260), "white")
d = ImageDraw.Draw(img)
fb = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 28)
f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
d.text((16, 10), "会议室设备清单", fill="black", font=fb)
d.text((16, 58), "投影仪 HDMI 线在前台借用。", fill="black", font=f)
d.rectangle([16, 100, 560, 200], outline="black", width=2)
d.line([16, 132, 560, 132], fill="black", width=2)
d.line([240, 100, 240, 200], fill="black", width=2)
d.text((28, 106), "设备", fill="black", font=f)
d.text((252, 106), "位置", fill="black", font=f)
d.text((28, 145), "投影仪", fill="black", font=f)
d.text((252, 145), "3 楼 A 室", fill="black", font=f)
buf = io.BytesIO()
img.save(buf, format="PNG")

t0 = time.time()
res = post("/v1/plugins/kb/api/import",
           {"name": "meeting-room.png", "data_b64": base64.b64encode(buf.getvalue()).decode()})
print(f"导入 png（OCR）耗时 {time.time() - t0:.1f}s | ok={res.get('ok')} chars={res.get('chars')}")

d2 = get("/v1/plugins/kb/api/search?" + urllib.parse.urlencode({"q": "会议室 投影仪 位置", "n": 2}))
for x in d2.get("hits") or []:
    print(f"  {x.get('score'):.3f} {x.get('cite')}")
    print("    ", str(x.get("text"))[:90].replace("\n", " "))

# kb_add 工具（通过插件工具直呼不了 HTTP，改看 api/import 的 text 路径等价）
r = post("/v1/plugins/kb/api/import", {"text": "会议室投影仪借用请找前台登记。", "title": "投影仪借用"})
print("text 入库:", r.get("ok"), r.get("title"))

html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8", "replace")
print("前端含 📚 按钮:", "kbSaveMsg" in html or "存入知识库" in html)
js = urllib.request.urlopen(BASE + "/ui/js/panels.js", timeout=30).read().decode("utf-8", "replace")
print("/kb 命令已在前端:", "知识库" in js and "kb_add" in js)
