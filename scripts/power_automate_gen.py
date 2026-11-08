# -*- coding: utf-8 -*-
"""Power Automate 工程生成 CLI（同租户、不调用真实 API）。

用法：
  python scripts/power_automate_gen.py spec.json --workspace workspace --target all
  python scripts/power_automate_gen.py --name demo --spec '{"trigger":{"type":"manual"},"actions":[...]}'

target: project / legacy / solution / all（默认 all）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_automate as pa  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Power Automate 工程与导入包")
    parser.add_argument("spec_file", nargs="?", help="spec JSON 文件路径")
    parser.add_argument("--name", default="", help="工程名（spec 里没有 name 时用）")
    parser.add_argument("--spec", default="", help="内联 JSON spec（与 spec_file 二选一）")
    parser.add_argument("--workspace", default="workspace", help="工作区根目录，默认 workspace")
    parser.add_argument("--output-dir", default="flows", help="输出目录（相对工作区），默认 flows")
    parser.add_argument("--target", default="all", choices=("project", "legacy", "solution", "all"))
    parser.add_argument("--publisher", default="GantryPublisher", help="解决方案发布者")
    parser.add_argument("--prefix", default="new", help="解决方案前缀")
    parser.add_argument("--version", default="1.0.0.0", help="解决方案版本")
    args = parser.parse_args()

    if args.spec_file:
        payload = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    elif args.spec:
        payload = json.loads(args.spec)
    else:
        parser.error("需要 spec_file 或 --spec")
    if args.name:
        payload.setdefault("name", args.name)
    if not payload.get("name"):
        parser.error("spec 缺少 name")

    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        project = pa.write_flow_project(workspace, payload, output_dir=args.output_dir)
        print(f"工程目录: {project['dir']}")
        if args.target in ("legacy", "all"):
            pkg = pa.build_legacy_package(workspace, payload, output_dir=args.output_dir)
            print(f"旧版导入包: {pkg}")
        if args.target in ("solution", "all"):
            pkg = pa.build_solution_package(workspace, payload, publisher=args.publisher,
                                            prefix=args.prefix, version=args.version,
                                            output_dir=args.output_dir)
            print(f"解决方案包: {pkg}")
        report = pa.validate_project(workspace, project["name"], output_dir=args.output_dir)
        print("校验:", "OK" if report["ok"] else report["problems"])
        return 0 if report["ok"] else 1
    except pa.PowerAutomateError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
