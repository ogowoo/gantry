# -*- coding: utf-8 -*-
"""分模块跑测试（每个模块独立进程 + 超时），用于排查"整体跑会卡住"的次序问题。

用法：
    python scripts/run_tests_chunked.py [--timeout 180] [--only 关键字]

输出：每个模块的 OK / FAILED / TIMEOUT，最后汇总。退出码非 0 表示有失败或超时。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
TESTS = BASE / "tests"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=180, help="单个模块超时（秒）")
    ap.add_argument("--only", default="", help="只跑名字含该关键字的模块")
    ap.add_argument("--skip", default="",
                    help="跳过名字含这些关键字的模块（逗号分隔）；用于 CI 上已知会挂的模块")
    args = ap.parse_args()

    mods = sorted(p.stem for p in TESTS.glob("test_*.py"))
    if args.only:
        mods = [m for m in mods if args.only in m]
    skips = [s.strip() for s in (args.skip or "").split(",") if s.strip()]
    if skips:
        dropped = [m for m in mods if any(s in m for s in skips)]
        mods = [m for m in mods if not any(s in m for s in skips)]
        if dropped:
            print(f"SKIP    {len(dropped)} 个模块（--skip {' '.join(skips)}）：{', '.join(dropped)}")
    bad = []
    t0 = time.time()
    # 与子进程内 tests/__init__.py 同一套 redirect：测试临时目录统一进仓库外的
    # .gantry-test-tmp，仓库根保持干净（tests/__init__ 还会再设一次，双保险）。
    _tmp_redirect = BASE.parent / ".gantry-test-tmp"
    _tmp_redirect.mkdir(parents=True, exist_ok=True)
    _child_env = dict(os.environ, TMPDIR=str(_tmp_redirect), TEMP=str(_tmp_redirect),
                      TMP=str(_tmp_redirect))
    for m in mods:
        cmd = [sys.executable, "-m", "unittest", f"tests.{m}"]
        started = time.time()
        try:
            r = subprocess.run(cmd, cwd=str(BASE), capture_output=True, text=True,
                               timeout=args.timeout, env=_child_env)
            out = (r.stderr or "") + (r.stdout or "")
            tail = [ln for ln in out.splitlines() if ln.startswith(("Ran ", "OK", "FAILED"))]
            status = "OK" if r.returncode == 0 else "FAILED"
            if status != "OK":
                bad.append(m)
            print(f"{status:7} {m:46} {time.time() - started:5.1f}s  {' | '.join(tail[-2:])}")
            if status != "OK":
                for ln in out.splitlines():
                    if ln.startswith(("FAIL:", "ERROR:")):
                        print("        ", ln[:120])
        except subprocess.TimeoutExpired:
            bad.append(m)
            print(f"TIMEOUT {m:46} {time.time() - started:5.1f}s  （>{args.timeout}s）")
    # 清空 redirect 目录里的临时内容；并兜底清理仓库根残留。
    # 前缀清单不自维护：直接从 tests/*.py 源码里抓所有 mkdtemp(prefix="...")
    # 字面值动态生成（200+ 种且会随新测试增长），再加 tempfile 默认前缀 "tmp"。
    import re
    import shutil
    n = 0
    for sub in _tmp_redirect.iterdir():
        shutil.rmtree(sub, ignore_errors=True)
        n += 1
    _prefixes = {"tmp"}
    for src in TESTS.glob("test_*.py"):
        try:
            _prefixes.update(re.findall(r'mkdtemp\(prefix="([^"]+)"', src.read_text(encoding="utf-8")))
        except OSError:
            pass
    for d in BASE.iterdir():
        if d.is_dir() and d.name.startswith(tuple(_prefixes)):
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    if n:
        print(f"（已清理 {n} 个测试临时目录）")
    print(f"\n共 {len(mods)} 个模块，用时 {time.time() - t0:.1f}s；异常模块：{bad or '无'}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
