# -*- coding: utf-8 -*-
"""文档 + 版本：ESC 修复、OCR（RapidOCR / PP-OCRv6）。"""
from pathlib import Path

# README
p = Path("README.md")
s = p.read_text(encoding="utf-8")
s = s.replace("- **drag & drop / choose files** — `docx`, `pdf`, `xlsx`, `xlsm`, `pptx`, `md`, `txt`, `csv`, `html`;",
              "- **drag & drop / choose files** — `docx`, `pdf`, `xlsx`, `xlsm`, `pptx`, `md`, `txt`, `csv`, `html`,\n  and images (`png/jpg/jpeg/bmp/webp/tif/tiff`);", 1)
s = s.replace("All extraction is **pure Python** (`python-docx`, `pypdf`, `openpyxl`, `python-pptx`, `beautifulsoup4`)\n— no external service.",
              "All extraction is **pure Python** (`python-docx`, `pypdf`, `openpyxl`, `python-pptx`, `beautifulsoup4`)\n— no external service. Scanned PDFs and images go through **RapidOCR** (ONNX, CPU, PP-OCRv5/6\nmodels, ~20 MB, `pip install rapidocr`); if it is not installed the import fails with a clear\n`pip install` hint instead of silently storing an empty document.", 1)
p.write_text(s, encoding="utf-8")
print("README: OCR 说明")

# plugins/kb/README
p = Path("plugins/kb/README.md")
s = p.read_text(encoding="utf-8")
s = s.replace("提取全部用 Python 库：`python-docx` / `pypdf` / `openpyxl` / `python-pptx` / `beautifulsoup4`；",
              "提取全部用 Python 库：`python-docx` / `pypdf` / `openpyxl` / `python-pptx` / `beautifulsoup4`；\n**扫描版 PDF 与图片**走 RapidOCR（ONNX、CPU、PP-OCRv6 模型，`pip install rapidocr`，首次运行自动下模型）；", 1)
p.write_text(s, encoding="utf-8")
print("plugins/kb/README: OCR")

# CHANGELOG preview.55
p = Path("CHANGELOG.md")
src = p.read_text(encoding="utf-8")
old = """## [11.0.0-preview.55] - 2026-11-05

### Added
- 

### Changed
- 

### Fixed
-
"""
new = """## [11.0.0-preview.55] - 2026-11-05

### Fixed

- **知识库管理页 Esc 关不掉**：`manual.js` 的全局 Esc 处理器漏了 `closeKb()`
  （其余弹窗都加了）。补上后实测：打开 → 按 Esc → 关闭。

### Added

- **OCR 兜底**（`gantry/kb_ocr.py`）：扫描版 PDF 与图片（png/jpg/jpeg/bmp/webp/tif/tiff）
  → **RapidOCR**（ONNX，CPU，PP-OCRv6 det/rec 小模型，`pip install rapidocr`，首次运行自动下载
  约 20 MB）。PDF 只有**没有文本层的页**才 OCR（正常 PDF 不受影响）；没装引擎时报
  `pip install rapidocr`，不会静默入库空文档。

### 实测

- 中文图片两行全部识别正确（"打印机脱机时先重启 Print Spooler 服务 / VPN 连不上请换手机热点试一次"），
  首次 3.93s（含加载），**0.72s/张**（复用引擎）。
- 图片型 PDF（fpdf 嵌图、无文本层）：`extract_text` 1.25s 返回
  `## 第 1 页（OCR） 扫描件测试：打印机脱机请重启服务`。
- `tests/test_kb_ingest.py` 26 条全绿（含图片 OCR、空结果、缺引擎提示、扫描 PDF 回退、
  有文本层 PDF 不触发 OCR）。
"""
assert old in src
p.write_text(src.replace(old, new, 1), encoding="utf-8")
print("CHANGELOG preview.55 已写入")
