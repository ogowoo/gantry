#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gantry 更新命令行（离线可用）。

用法：
  python scripts/update.py status
  python scripts/update.py check [--method auto|git|zip]
  python scripts/update.py apply [--version X] [--method ...] [--force] [--dry-run]
  python scripts/update.py list
  python scripts/update.py rollback [--backup ID]

安装完成后需要重启服务；API/WebUI 会排定自动重启，CLI 只提示。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gantry.updater import UpdateError, from_config  # noqa: E402


def _print(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    ap = argparse.ArgumentParser(description="Gantry 更新系统")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "check", "list"):
        sub.add_parser(name)
    p_check = sub.choices["check"]
    p_check.add_argument("--method", default="", choices=["", "auto", "git", "zip"])
    p_apply = sub.add_parser("apply")
    p_apply.add_argument("--version", default="")
    p_apply.add_argument("--method", default="", choices=["", "auto", "git", "zip"])
    p_apply.add_argument("--force", action="store_true")
    p_apply.add_argument("--dry-run", action="store_true")
    p_roll = sub.add_parser("rollback")
    p_roll.add_argument("--backup", default="")
    args = ap.parse_args()

    try:
        up = from_config()
        if args.cmd == "status":
            _print(up.status())
        elif args.cmd == "check":
            _print(up.check(method=args.method))
        elif args.cmd == "list":
            _print({"backups": up.backups()})
        elif args.cmd == "apply":
            out = up.apply(version=args.version, method=args.method,
                           restart=False, dry_run=args.dry_run, force=args.force)
            _print(out)
            if out.get("changed") and not args.dry_run:
                print("\n代码已更新，请重启服务：python start.py")
        elif args.cmd == "rollback":
            out = up.rollback(backup=args.backup)
            _print(out)
            print("\n已回滚代码，请重启服务：python start.py")
        return 0
    except UpdateError as exc:
        print(f"更新失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
