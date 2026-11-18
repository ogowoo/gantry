# -*- coding: utf-8 -*-
"""实测 Xiaomi-OCR-0（0.8B, Q4_K_M + mmproj）在 llama_cpp 里能否跑图片 OCR。

先试 Qwen25VLChatHandler（llama-cpp-python 提供的最接近的视觉处理器），
再退到 MiniCPMv26ChatHandler；都失败就记录真实报错（这决定要不要走 Ollama）。
"""
import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont

MODEL = r"E:\models\gguf\xiaomi-ocr0\Xiaomi-OCR-0.Q4_K_M.gguf"
MMPROJ = r"E:\models\gguf\xiaomi-ocr0\Xiaomi-OCR-0.mmproj-bf16.gguf"

# 造一张"文档截图"：带标题、两行正文、一个表格
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
png = buf.getvalue()
uri = "data:image/png;base64," + base64.b64encode(png).decode()

PROMPT = ("Extract all information from the main body of the document image and represent it "
          "in markdown format, ignoring headers and footers.")

def try_handler(cls_name: str) -> None:
    import llama_cpp.llama_chat_format as fmt

    cls = getattr(fmt, cls_name)
    print(f"\n== 试 {cls_name}")
    handler = cls(clip_model_path=MMPROJ, verbose=False)
    t0 = time.time()
    llm = __import__("llama_cpp").Llama(model_path=MODEL, chat_handler=handler,
                                       n_ctx=4096, n_threads=12, verbose=False)
    print(f"加载 {time.time() - t0:.1f}s")
    t0 = time.time()
    out = llm.create_chat_completion(
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": uri}},
            {"type": "text", "text": PROMPT}]}],
        max_tokens=600, temperature=0)
    el = time.time() - t0
    text = out["choices"][0]["message"].get("content") or ""
    print("finish_reason:", out["choices"][0].get("finish_reason"),
          "| raw keys:", list(out["choices"][0].get("message", {}).keys()))
    toks = (out.get("usage") or {}).get("completion_tokens") or 0
    print(f"生成 {toks} tok / {el:.1f}s = {toks / el:.1f} tok/s")
    print("输出:\n" + text[:800])
    del llm


for name in ("MiniCPMv26ChatHandler", "Qwen25VLChatHandler"):
    try:
        try_handler(name)
        break
    except Exception as exc:                     # noqa: BLE001
        print(f"✗ {name} 失败: {type(exc).__name__}: {str(exc)[:220]}")
