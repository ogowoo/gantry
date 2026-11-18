# -*- coding: utf-8 -*-
"""线上验证 auto：① 普通文字截图应走 RapidOCR（快）；② 表格截图应自动转 Xiaomi（OTSL）。"""
import base64
import io
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(p, body, t=1800):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


def get(p, t=600):
    return json.loads(urllib.request.urlopen(BASE + p, timeout=t).read())


from PIL import Image, ImageDraw, ImageFont  # noqa: E402

f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)


def png(img) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ① 普通正文（预期：RapidOCR，快）
text_img = Image.new("RGB", (900, 260), "white")
d = ImageDraw.Draw(text_img)
body = ("IT 服务台值班说明：工作日上午 9 点到下午 6 点在岗；"
        "报修请拨 8000，紧急故障转 3；非工作时段走值班手机。")
d.text((20, 20), body[:26], fill="black", font=f)
d.text((20, 60), body[26:52], fill="black", font=f)
d.text((20, 100), body[52:80], fill="black", font=f)

t0 = time.time()
r1 = post("/v1/plugins/kb/api/import", {"name": "auto-text.png", "data_b64": png(text_img)})
print(f"① 普通文字：{time.time() - t0:.1f}s ok={r1.get('ok')} chars={r1.get('chars')}")

# ② 表格（预期：auto → Xiaomi，OTSL）
tbl = Image.new("RGB", (760, 300), "white")
d = ImageDraw.Draw(tbl)
fb = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 26)
d.text((16, 8), "VPN 故障速查表", fill="black", font=fb)
d.rectangle([16, 60, 640, 200], outline="black", width=2)
d.line([16, 100, 640, 100], fill="black", width=2)
d.line([300, 60, 300, 200], fill="black", width=2)
d.text((28, 68), "现象", fill="black", font=f)
d.text((312, 68), "处理", fill="black", font=f)
d.text((28, 112), "无法建立隧道", fill="black", font=f)
d.text((312, 112), "换 443 端口", fill="black", font=f)
d.text((28, 152), "连上没网", fill="black", font=f)
d.text((312, 152), "清 DNS 缓存", fill="black", font=f)

t0 = time.time()
r2 = post("/v1/plugins/kb/api/import", {"name": "auto-table.png", "data_b64": png(tbl)})
print(f"② 表格页：{time.time() - t0:.1f}s ok={r2.get('ok')} chars={r2.get('chars')}")

d2 = get("/v1/plugins/kb/api/search?" + urllib.parse.urlencode({"q": "无法建立隧道 换端口", "n": 3}))
for h in d2.get("hits") or []:
    t = str(h.get("text"))
    mark = "OTSL" if ("<fcel>" in t or "<nl>" in t) else "text"
    print(f"  {h.get('score'):.3f} [{mark}] {h.get('cite')}")
    print("   ", t[:120].replace("\n", " ⏎ "))
