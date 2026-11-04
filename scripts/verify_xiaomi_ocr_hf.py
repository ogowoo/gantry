# -*- coding: utf-8 -*-
"""用官方组合（transformers 5.17 + torch 2.14 CPU）跑 Xiaomi-OCR-0，测中文文档图片。

模型是 0.8B bf16（safetensors 约 1.6 GB）；CPU 上：
- 权重用 bf16 省内存，计算由 torch CPU 自动处理；
- 给一张 760x320 的"文档截图"（标题+两行正文+表格），看能否输出 Markdown、耗时多少。
"""
import io
import time

import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import AutoModelForImageTextToText, AutoProcessor

MODEL = "SeerRay-Lab/Xiaomi-OCR-0"
PROMPT = ("Extract all information from the main body of the document image and represent it "
          "in markdown format, ignoring headers and footers. Tables should be expressed in "
          "OTSL format, formulas in the document should be represented using LATEX format, and "
          "the parsing should be organized according to the reading order.")

img = Image.new("RGB", (760, 320), "white")
d = ImageDraw.Draw(img)
f_big = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 30)
f = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
d.text((20, 12), "电脑蓝屏处理手册", fill="black", font=f_big)
d.text((20, 70), "1. 记录蓝屏代码，如 0x0000007B。", fill="black", font=f)
d.text((20, 104), "2. 检查硬盘模式是否为 AHCI。", fill="black", font=f)
d.rectangle([20, 150, 600, 250], outline="black", width=2)
d.line([20, 185, 600, 185], fill="black", width=2)
d.line([250, 150, 250, 250], fill="black", width=2)
d.text((35, 158), "现象", fill="black", font=f)
d.text((265, 158), "处理", fill="black", font=f)
d.text((35, 200), "蓝屏 7B", fill="black", font=f)
d.text((265, 200), "改 AHCI", fill="black", font=f)
buf = io.BytesIO()
img.save(buf, format="PNG")
buf.seek(0)

t0 = time.time()
processor = AutoProcessor.from_pretrained(MODEL)
print(f"processor 加载 {time.time() - t0:.1f}s")
t0 = time.time()
model = AutoModelForImageTextToText.from_pretrained(
    MODEL, dtype=torch.bfloat16, low_cpu_mem_usage=True).eval()
print(f"模型加载 {time.time() - t0:.1f}s")

messages = [{"role": "user", "content": [
    {"type": "image", "image": img},
    {"type": "text", "text": PROMPT}]}]
inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                       return_dict=True, return_tensors="pt")
print("输入 token:", int(inputs["input_ids"].shape[1]))
t0 = time.time()
with torch.inference_mode():
    out = model.generate(**inputs, max_new_tokens=600, do_sample=False)
el = time.time() - t0
new = out[:, inputs["input_ids"].shape[1]:]
text = processor.batch_decode(new, skip_special_tokens=True)[0]
print(f"生成 {new.shape[1]} tok / {el:.1f}s | 含'蓝屏':", "蓝屏" in text, "| 含表格标记:",
      ("OTSL" in text or "<table" in text or "|" in text))
print("输出:", text[:700].replace("\n", " ⏎ "))
