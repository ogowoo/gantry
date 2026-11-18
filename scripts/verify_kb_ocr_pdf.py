# -*- coding: utf-8 -*-
"""扫描版 PDF：用 fpdf 把图片嵌进 PDF（无文本层），验证 kb_ingest 自动回退 OCR。"""
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

TEXT = ["扫描件测试：打印机脱机请重启服务"]
img = Image.new("RGB", (760, 90), "white")
draw = ImageDraw.Draw(img)
font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 30)
draw.text((16, 22), TEXT[0], fill="black", font=font)
png_path = Path("data/_scan_probe.png")
png_path.parent.mkdir(parents=True, exist_ok=True)
img.save(png_path, format="PNG")

from fpdf import FPDF  # noqa: E402

pdf = FPDF()
pdf.add_page()
pdf.image(str(png_path), x=10, y=10, w=180)
pdf_bytes = bytes(pdf.output())
print("PDF 字节:", len(pdf_bytes))

from gantry import kb_ingest, kb_ocr  # noqa: E402

print("OCR 可用:", kb_ocr.available())
t0 = time.time()
text = kb_ingest.extract_text("scan.pdf", pdf_bytes)
print(f"extract_text 耗时 {time.time() - t0:.2f}s")
print("含'打印机':", "打印机" in text, "| 含 'OCR' 标注:", "OCR" in text)
print(text[:200].replace("\n", " / "))
png_path.unlink(missing_ok=True)
