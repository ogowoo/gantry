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
    """对一张图片做 OCR，返回按行拼接的文本。"""
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
