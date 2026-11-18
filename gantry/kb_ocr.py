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


_CFG: dict[str, Any] = {"engine": "rapidocr",
                        "xiaomi_model": "SeerRay-Lab/Xiaomi-OCR-0",
                        "auto_sparse_chars": 40,   # 每百万像素少于这么多字就转 Xiaomi
                        "auto_table_rows": 2}      # 至少这么多行、每行 ≥2 框 → 视为表格
_XIAOMI: Any = None


def configure(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """设置 OCR 引擎：`{"engine": "rapidocr"|"xiaomi", "xiaomi_model": "..."}`。

    插件启动时从 config.json 的 kb.ocr 段调用；不配就用 RapidOCR。
    """
    c = dict(cfg or {})
    name = str(c.get("engine") or "rapidocr").lower()
    if name in ("default", ""):
        name = "rapidocr"
    if name not in ("rapidocr", "xiaomi", "auto"):
        name = "rapidocr"
    _CFG["engine"] = name
    if c.get("xiaomi_model"):
        _CFG["xiaomi_model"] = str(c["xiaomi_model"])
    for k in ("auto_sparse_chars", "auto_table_rows"):
        if c.get(k) not in (None, ""):
            _CFG[k] = c[k]
    return dict(_CFG)


def config() -> dict[str, Any]:
    return dict(_CFG)


def available() -> bool:
    """RapidOCR 是否可用（默认引擎）。"""
    try:
        import rapidocr  # noqa: F401
        return True
    except Exception:                            # noqa: BLE001
        return False


def available_xiaomi() -> bool:
    """Xiaomi-OCR-0 的依赖是否装了（transformers>=5.17 + torch）。"""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except Exception:                            # noqa: BLE001
        return False


def effective_engine() -> str:
    """当前配置下**实际**会用的引擎（auto 时按可用性降级，提前告诉调用方）。"""
    name = str(_CFG.get("engine") or "rapidocr")
    if name == "rapidocr":
        return "rapidocr"
    if name == "xiaomi":
        return "xiaomi"
    # auto：优先 RapidOCR；没有就 Xiaomi；都没有保持 auto（调用时报错）
    if available():
        return "auto→rapidocr"
    if available_xiaomi():
        return "auto→xiaomi"
    return "auto(无可用引擎)"


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
    if _CFG.get("engine") == "auto" and not available():
        if available_xiaomi():
            return xiaomi_image_bytes(data)
        raise IngestError(
            "没有可用的 OCR 引擎：装任一个即可 —— "
            "`pip install rapidocr`（20 MB，快）或 `pip install -U transformers torch`"
            "（0.8B VLM，表格/公式更好）")
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
    lines, boxes = _rapidocr_parts(out)
    text = "\n".join(lines).strip()
    if not text:
        if _CFG.get("engine") == "auto":
            return _auto_xiaomi(data, img.size, "", [])
        raise IngestError("OCR 没有识别出文字（图片可能太模糊或没有文字）")
    if _CFG.get("engine") == "auto" and _needs_xiaomi(text, boxes, img.size):
        return _auto_xiaomi(data, img.size, text, boxes)
    return text[:MAX_TEXT_CHARS]


def _rapidocr_parts(out: Any) -> tuple[list[str], list[Any]]:
    """兼容 RapidOCR 3.x（.txts/.boxes）与旧版 (rows, elapse) 元组。"""
    lines: list[str] = []
    boxes: list[Any] = []
    txts = getattr(out, "txts", None)
    box_attr = getattr(out, "boxes", None)
    if txts is not None:
        lines = [str(t) for t in txts if str(t).strip()]
        if box_attr is not None:
            boxes = list(box_attr)
    elif isinstance(out, tuple) and out:
        rows = out[0] or []
        for r in rows:
            if len(r) > 2 and str(r[1]).strip():
                boxes.append(r[0])
                lines.append(str(r[1]))
    return lines, boxes


def _box_center(box: Any) -> tuple[float, float, float]:
    """框 → (中心 x, 中心 y, 高度)；兼容 4 点/矩形两种写法。"""
    try:
        pts = list(box)
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return (sum(xs) / len(xs), sum(ys) / len(ys),
                max(ys) - min(ys) or 1.0)
    except Exception:                            # noqa: BLE001
        return (0.0, 0.0, 1.0)


def _table_like(boxes: list[Any]) -> bool:
    """按垂直中心分行，若有足够的"多框行"就认为像表格（表格/表单/PPT 版式）。"""
    if len(boxes) < 4:
        return False
    rows: list[list[tuple[float, float]]] = []
    for b in boxes:
        cx, cy, h = _box_center(b)
        for row in rows:
            if abs(row[0][1] - cy) <= max(h, row[0][2]) * 0.6:
                row.append((cx, cy, h))
                break
        else:
            rows.append([(cx, cy, h)])
    multi = [r for r in rows if len(r) >= 2]
    need = int(_CFG.get("auto_table_rows") or 2)
    return len(multi) >= need


def _needs_xiaomi(text: str, boxes: list[Any], size: tuple[int, int]) -> bool:
    """文本太稀 或 版面像表格 → 值得花 6 秒让 Xiaomi-OCR-0 重新解析。"""
    w, h = size
    mp = max(0.01, (int(w) * int(h)) / 1_000_000)
    threshold = float(_CFG.get("auto_sparse_chars") or 40)
    if len(text) / mp < threshold:
        return True
    return _table_like(boxes)


def _auto_xiaomi(data: bytes, size: tuple[int, int], fallback: str,
                boxes: list[Any]) -> str:
    """auto 模式下调用 Xiaomi；不可用/失败时回退到 RapidOCR 文本。"""
    try:
        return xiaomi_image_bytes(data)
    except Exception:                            # noqa: BLE001
        if fallback.strip():
            return fallback[:MAX_TEXT_CHARS]
        w, h = size
        raise IngestError(
            "RapidOCR 在这张图上没识别出文字，且 Xiaomi-OCR-0 不可用"
            f"（{w}x{h}）。装它：pip install -U transformers torch，"
            "或在 config.json 把 kb.ocr.engine 设成 xiaomi") from None


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


def warm() -> dict[str, Any]:
    """预热引擎：按当前配置加载 RapidOCR / Xiaomi-OCR-0，返回状态（不抛异常）。"""
    import time

    t0 = time.time()
    out: dict[str, Any] = {"engine": _CFG.get("engine"), "ok": True}
    try:
        if _CFG.get("engine") in ("xiaomi", "auto"):
            model = str(_CFG.get("xiaomi_model"))
            _load_xiaomi(model)
            out["xiaomi"] = "loaded"
        if _CFG.get("engine") in ("rapidocr", "auto"):
            _engine()
            out["rapidocr"] = "loaded"
    except Exception as exc:                     # noqa: BLE001
        out["ok"] = False
        out["error"] = str(exc)[:200]
    out["seconds"] = round(time.time() - t0, 1)
    return out


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
