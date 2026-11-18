# -*- coding: utf-8 -*-
"""线上验证：通过 kb 插件接口导入 docx / pdf / 文本 / URL，再检索、删除。"""
import base64
import io
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"


def post(path, body, t=600):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


def get(path, t=300):
    return json.loads(urllib.request.urlopen(BASE + path, timeout=t).read())


# 重载插件（新增了接口）
req = urllib.request.Request(BASE + "/v1/plugins/kb/reload", data=b"{}",
                             headers={"Content-Type": "application/json"})
print("reload:", json.loads(urllib.request.urlopen(req, timeout=180).read())["plugin"]["status"])

# 1) docx
from docx import Document  # noqa: E402

d = Document()
d.add_heading("打印服务使用说明", level=1)
d.add_heading("常见问题", level=2)
d.add_paragraph("打印机脱机时，先重启 Print Spooler 服务，再重新连接。")
buf = io.BytesIO()
d.save(buf)
res = post("/v1/plugins/kb/api/import",
           {"name": "printer.docx", "data_b64": base64.b64encode(buf.getvalue()).decode()})
print("docx:", res.get("ok"), res.get("title"), res.get("chars"), "| docs:", (res.get("stats") or {}).get("documents"))

# 2) pdf
from fpdf import FPDF  # noqa: E402

pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", size=12)
pdf.cell(0, 10, "Guest Wi-Fi onboarding: connect to Example-Guest, password at reception")
res = post("/v1/plugins/kb/api/import",
           {"name": "guest-wifi.pdf", "data_b64": base64.b64encode(bytes(pdf.output())).decode()})
print("pdf :", res.get("ok"), res.get("title"), res.get("chars"))

# 3) 文本
res = post("/v1/plugins/kb/api/import",
           {"text": "会议室预订：在 Outlook 里选资源邮箱 CN-SH-Room-A，提前 15 分钟取消不算占用。",
            "title": "会议室预订"})
print("text:", res.get("ok"), res.get("title"), res.get("chars"))

# 4) 检索
for q in ("打印机脱机", "访客 WiFi 密码", "会议室怎么订"):
    d = get("/v1/plugins/kb/api/search?" + urllib.parse.urlencode({"q": q, "n": 2}))
    top = (d.get("hits") or [{}])[0]
    print(f"search {q!r}: backend={d.get('backend')} count={d.get('count')} "
          f"top={top.get('cite')} score={top.get('score')}")

# 5) 删除（删掉 pdf）
docs = get("/v1/plugins/kb/api/docs")
target = [x for x in docs.get("documents") or [] if "guest-wifi" in x["path"]]
if target:
    print("delete:", post("/v1/plugins/kb/api/delete", {"path": target[0]["path"]}).get("ok"),
          "| docs now:", post("/v1/plugins/kb/api/reindex", {}) and get("/v1/plugins/kb/api/stats").get("documents"))

# 6) 收件箱
print("inbox:", get("/v1/plugins/kb/api/inbox").get("dir"),
      get("/v1/plugins/kb/api/inbox").get("count"))

# 7) 页面静态资源
html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8", "replace")
print("index.html 含 kbMask:", 'id="kbMask"' in html)
js = urllib.request.urlopen(BASE + "/js/kb.js", timeout=30).read().decode("utf-8", "replace")
print("kb.js 可访问:", "openKb" in js, "| 字节:", len(js))
