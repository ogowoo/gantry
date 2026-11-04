# -*- coding: utf-8 -*-
"""真机验证 OCR：造一张中文图片（PIL + 微软雅黑）→ RapidOCR 识别 → 时间/文本。

再做一个"扫描版 PDF"（只有图片的页）走 kb_ingest.extract_text，确认自动回退 OCR。
"""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

TEXT = ["打印机脱机时先重启 Print Spooler 服务", "VPN 连不上请换手机热点试一次"]

img = Image.new("RGB", (900, 140), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 32)
for i, line in enumerate(TEXT):
    draw.text((20, 15 + i * 52), line, fill="black", font=font)
buf = io.BytesIO()
img.save(buf, format="PNG")
png = buf.getvalue()
print("图片字节:", len(png))

from gantry import kb_ocr  # noqa: E402

print("OCR 可用:", kb_ocr.available())
t0 = time.time()
text = kb_ocr.ocr_image_bytes(png)
el = time.time() - t0
print(f"识别耗时 {el:.2f}s")
print("识别结果:")
for line in text.splitlines():
    print("   ", line)

# 再来一次（引擎已加载，看纯推理时间）
t0 = time.time()
kb_ocr.ocr_image_bytes(png)
print(f"第二次耗时 {time.time() - t0:.2f}s")

# 扫描版 PDF：把图片塞进 PDF 页面，再走 kb_ingest
from pypdf import PdfWriter  # noqa: E402

writer = PdfWriter()
page = writer.add_blank_page(width=900, height=140)
page.images.append(Image.open(io.BytesIO(png)))
pdf_buf = io.BytesIO()
writer.write(pdf_buf)
data = pdf_buf.getvalue()
print("扫描 PDF 字节:", len(data))

from gantry import kb_ingest  # noqa: E402

t0 = time.time()
pdf_text = kb_ingest.extract_text("scanned.pdf", data)
print(f"PDF→OCR 耗时 {time.time() - t0:.2f}s | 含'打印机':", "打印机" in pdf_text)
print(pdf_text[:160].replace("\n", " / "))
