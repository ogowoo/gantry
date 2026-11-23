# -*- coding: utf-8 -*-
"""测试包初始化：把测试临时目录统一 redirect 到仓库外的固定位置。

本机沙箱里原生进程写不了系统 Temp，tempfile 兜底指向 cwd（仓库根），
几百个测试的 mkdtemp 会在仓库根撒出十几种前缀的临时目录。这里在导入期
（python -m unittest tests.x / pytest tests/ 都会先导入本包）把
TMPDIR/TEMP/TMP 指到仓库旁的 .gantry-test-tmp 并清掉 gettempdir 缓存，
仓库根从此干净；该目录由 scripts/run_tests_chunked.py 跑完清空。
"""
import os
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
# 优先仓库外（仓库根看着干净），不可写时退回仓库内 .test-tmp（gitignore 已覆盖）
_CANDIDATES = (_REPO_ROOT.parent / ".gantry-test-tmp",
               _REPO_ROOT / ".test-tmp")


def _redirect_tempdir() -> None:
    target = next((c for c in _CANDIDATES if _usable(c)), None)
    if target is None:
        return
    for var in ("TMPDIR", "TEMP", "TMP"):
        os.environ[var] = str(target)
    tempfile.tempdir = None          # 强制 gettempdir 按新 env 重算


def _usable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".probe"
        probe.write_text("x", encoding="ascii")
        probe.unlink()
        return True
    except OSError:
        return False


_redirect_tempdir()
