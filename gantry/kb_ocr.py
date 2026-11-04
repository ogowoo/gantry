# -*- coding: utf-8 -*-
"""OCR 兜底：扫描版 PDF / 图片 → 文本，用 RapidOCR（ONNX，CPU，纯 Python，中文强）。

设计要点：
- **可选依赖**：没装 `rapidocr` 时给出可行动的报错（`pip install rapidocr`），不拖垮导入其它格式；
- PDF 只 OCR **没有文本层的页**（每页取内嵌图，避免依赖 PDF 渲染器）；
- 直接导入 `.png/.jpg/.jpeg/.bmp/.webp/.tif/.tiff` 也走 OCR；
- 懒加载引擎（首次调用才加载模型，约 1-2s），单例缓存。
"""
from __future__ import annotations

import io
import threading
from typing import Any

from .kb_ingest import IngestError, MAX_TEXT_CHARS

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}

_ENGINE: Any = None
_LOCK = threading.Lock()


_CFG: dict[str, Any] = {"engine": "rapidocr", "xiaomi_model": "SeerRay-Lab/Xiaomi-OCR-0"}
_XIAOMI: Any = None


def configure(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """设置 OCR 引擎：`{"engine": "rapidocr"|"xiaomi", "xiaomi_model": "..."}`。

    插件启动时从 config.json 的 kb.ocr 段调用；不配就用 RapidOCR。
    """
    c = dict(cfg or {})
    name = str(c.get("engine") or "rapidocr").lower()
    if name in ("auto", "default", ""):
        name = "rapidocr"
    if name not in ("rapidocr", "xiaomi"):
        name = "rapidocr"
    _CFG["engine"] = name
    if c.get("xiaomi_model"):
        _CFG["xiaomi_model"] = str(c["xiaomi_model"])
    return dict(_CFG)


def config() -> dict[str, Any]:
    return dict(_CFG)


def available() -> bool:
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:                            # noqa: BLE001
        return False


def _engine():
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        from rapidocr import RapidOCR
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(
            "这台机器没装 OCR 引擎。扫描版 PDF/图片需要 OCR："
            "`pip install rapidocr`（ONNX，CPU 可用，模型首次运行自动下载约 20 MB）"
            f"；原始错误：{str(exc)[:120]}") from exc
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = RapidOCR()
    return _ENGINE


def set_engine(engine: Any) -> None:
    """测试用：注入假引擎（传 None 复位）。"""
    global _ENGINE
    _ENGINE = engine


def ocr_image_bytes(data: bytes) -> str:
    """对一张图片做 OCR：按配置用 RapidOCR 或 Xiaomi-OCR-0。"""
    if _CFG.get("engine") == "xiaomi":
        return xiaomi_image_bytes(data)
    try:
        from PIL import Image
        import numpy as np
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"OCR 需要 Pillow/numpy：{str(exc)[:120]}") from exc
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"图片无法解码：{str(exc)[:120]}") from exc
    engine = _engine()
    try:
        out = engine(np.array(img))
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"OCR 失败：{str(exc)[:160]}") from exc
    # RapidOCR 3.x 返回对象（.txts）或旧版 (result, elapse) 元组
    lines: list[str] = []
    txts = getattr(out, "txts", None)
    if txts is not None:
        lines = [str(t) for t in txts if str(t).strip()]
    elif isinstance(out, tuple) and out:
        rows = out[0] or []
        lines = [str(r[1]) for r in rows if len(r) > 1 and str(r[1]).strip()]
    text = "\n".join(lines).strip()
    if not text:
        raise IngestError("OCR 没有识别出文字（图片可能太模糊或没有文字）")
    return text[:MAX_TEXT_CHARS]


def ocr_file_bytes(data: bytes) -> str:
    """图片文件的入口（按扩展名在调用方判断）。"""
    return ocr_image_bytes(data)


def ocr_pdf_bytes(data: bytes) -> str:
    """扫描版 PDF：逐页取内嵌图片做 OCR（无文本层的页才做）。"""
    try:
        from pypdf import PdfReader
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"需要 pypdf：{str(exc)[:120]}") from exc
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:                        # noqa: BLE001
            text = ""
        if text:
            pages.append(f"## 第 {i} 页\n{text}")
            continue
        try:
            images = list(page.images)
        except Exception:                        # noqa: BLE001
            images = []
        got = []
        for img in images:
            try:
                got.append(ocr_image_bytes(bytes(img.data)))
            except IngestError:
                continue
        if got:
            pages.append(f"## 第 {i} 页（OCR）\n" + "\n".join(got))
    if not pages:
        raise IngestError("这个 PDF 没有文本层，OCR 也没识别出内容（可能是空白页或图片质量太差）")
    return "\n\n".join(pages)[:MAX_TEXT_CHARS]


# ── Xiaomi-OCR-0（0.8B VLM，文档解析/表格/公式）────────────────────────────
XIAOMI_PROMPT = (
    "Extract all information from the main body of the document image and represent it in "
    "markdown format, ignoring headers and footers. Tables should be expressed in OTSL format, "
    "formulas in the document should be represented using LATEX format, and the parsing should "
    "be organized according to the reading order.")


def _load_xiaomi(model_name: str):
    """懒加载 Xiaomi-OCR-0（CPU 上 bf16 权重，约 1.6 GB 内存）。"""
    global _XIAOMI
    if _XIAOMI is not None:
        return _XIAOMI
    try:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(
            "Xiaomi-OCR-0 需要 transformers>=5.17 + torch>=2.14："
            f"pip install -U transformers torch（当前不可用：{str(exc)[:120]}）") from exc
    with _LOCK:
        if _XIAOMI is None:
            try:
                processor = AutoProcessor.from_pretrained(model_name)
                model = AutoModelForImageTextToText.from_pretrained(
                    model_name, dtype=torch.bfloat16, low_cpu_mem_usage=True).eval()
            except Exception as exc:             # noqa: BLE001
                raise IngestError(f"加载 {model_name} 失败：{str(exc)[:200]}") from exc
            _XIAOMI = (processor, model, torch)
    return _XIAOMI


def set_xiaomi(bundle: Any) -> None:
    """测试用：注入假的 (processor, model, torch) 三元组。"""
    global _XIAOMI
    _XIAOMI = bundle


def xiaomi_image_bytes(data: bytes) -> str:
    """用 Xiaomi-OCR-0 解析整页文档图片，返回 Markdown（表格 OTSL / 公式 LaTeX）。"""
    try:
        from PIL import Image
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"需要 Pillow：{str(exc)[:120]}") from exc
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"图片无法解码：{str(exc)[:120]}") from exc
    processor, model, torch = _load_xiaomi(str(_CFG.get("xiaomi_model")))
    messages = [{"role": "user", "content": [
        {"type": "image", "image": img},
        {"type": "text", "text": XIAOMI_PROMPT}]}]
    try:
        inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                               return_dict=True, return_tensors="pt")
        with torch.inference_mode():
            out = model.generate(**inputs, max_new_tokens=2048, do_sample=False)
        new = out[:, inputs["input_ids"].shape[1]:]
        text = processor.batch_decode(new, skip_special_tokens=True)[0]
    except Exception as exc:                     # noqa: BLE001
        raise IngestError(f"Xiaomi-OCR-0 解析失败：{str(exc)[:200]}") from exc
    text = str(text or "").strip()
    if not text:
        raise IngestError("Xiaomi-OCR-0 没有输出内容（图片可能不是文档）")
    return text[:MAX_TEXT_CHARS]
