"""安装脚本：基础依赖 + Playwright 浏览器；OCR 为可选（--ocr / --xiaomi / --all）。

用法：
    python install.py                 # 基础（服务 + 浏览器自动化）
    python install.py --ocr           # 额外装 RapidOCR（扫描件/图片，20 MB）
    python install.py --xiaomi        # 额外装 Xiaomi-OCR-0 依赖（torch/transformers，较大）
    python install.py --all           # 基础 + Xiaomi（已含 rapidocr）
    python install.py --no-browser    # 跳过 Playwright 浏览器下载（离线/已有浏览器）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def pip(*args: str) -> None:
    cmd = [sys.executable, "-m", "pip", "install", *args]
    print("+", " ".join(cmd))
    subprocess.check_call(cmd)


def has(module: str) -> bool:
    try:
        __import__(module)
        return True
    except Exception:                            # noqa: BLE001
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Install Gantry dependencies")
    ap.add_argument("--ocr", action="store_true",
                    help="额外安装 RapidOCR（扫描件/图片 OCR，约 20 MB 模型）")
    ap.add_argument("--xiaomi", action="store_true",
                    help="额外安装 Xiaomi-OCR-0 依赖（torch + transformers，用于表格/公式解析）")
    ap.add_argument("--all", action="store_true", help="= --xiaomi（已含 rapidocr）")
    ap.add_argument("--no-browser", action="store_true", help="跳过 Playwright 浏览器下载")
    args = ap.parse_args()

    pip("-r", str(BASE / "requirements.txt"))
    if args.xiaomi or args.all:
        pip("-r", str(BASE / "requirements-ocr-xiaomi.txt"))
    elif args.ocr:
        pip("-r", str(BASE / "requirements-ocr.txt"))

    if not args.no_browser:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])

    ocr_ok = has("rapidocr")
    xiaomi_ok = has("torch") and has("transformers")
    print("\n安装完成。能力检查：")
    print(f"  Playwright 浏览器: {'已跳过' if args.no_browser else '已安装'}")
    print(f"  RapidOCR（扫描件/图片）: {'✓' if ocr_ok else '✗  需要时执行 python install.py --ocr'}")
    print(f"  Xiaomi-OCR-0（表格/公式）: {'✓' if xiaomi_ok else '✗  需要时执行 python install.py --xiaomi'}")
    print("\n下一步：python start.py" + ("" if ocr_ok else "   （纯文字知识库不需要 OCR，可先跳过）"))
    if shutil.which("python") is None:
        print("提示：命令里的 python 若不可用，请用实际解释器路径。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
