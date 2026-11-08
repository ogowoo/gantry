# -*- coding: utf-8 -*-
"""生成 Power Automate 连接器 operationId 验证包（每个连接器一个最小流）。

用途：把 docs/POWER_AUTOMATE_DEV.md 里的“文档值”变成“实测值”。
每个包只含一个连接器动作，导入后能验证 operationId 是否有效。

用法：
  python scripts/power_automate_verify.py
  python scripts/power_automate_verify.py --only outlook_send_email
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_automate as pa  # noqa: E402

CASES: dict[str, dict] = {
    "outlook_send_email": {
        "name": "verify-outlook-send-email",
        "display_name": "验证 Outlook 发邮件",
        "action": {"connector": "outlook", "operation": "send_email",
                   "parameters": {"emailMessage/To": "<占位：收件人>",
                                  "emailMessage/Subject": "<占位：主题>",
                                  "emailMessage/Body": "<占位：正文>"}},
        "connection": "Office 365 Outlook",
    },
    "sharepoint_get_items": {
        "name": "verify-sharepoint-get-items",
        "display_name": "验证 SharePoint 读列表",
        "action": {"connector": "sharepoint", "operation": "get_items",
                   "parameters": {"dataset": "<占位：站点 URL>",
                                  "table": "<占位：列表名>"}},
        "connection": "SharePoint",
    },
    "excel_list_rows": {
        "name": "verify-excel-list-rows",
        "display_name": "验证 Excel 读表格行",
        "action": {"connector": "excel", "operation": "list_rows",
                   "parameters": {"source": "<占位：文档库>", "drive": "<占位：驱动器>",
                                  "file": "<占位：工作簿>", "table": "<占位：表名>"}},
        "connection": "Excel Online (Business)",
    },
    "office365users_get_profile": {
        "name": "verify-office365users-profile",
        "display_name": "验证 Office 365 Users 读资料",
        "action": {"connector": "office365users", "operation": "get_profile",
                   "parameters": {"userPrincipalName": "<占位：用户 UPN>"}},
        "connection": "Office 365 Users",
    },
    "dataverse_list_records": {
        "name": "verify-dataverse-list-records",
        "display_name": "验证 Dataverse 查询",
        "action": {"connector": "dataverse", "operation": "list_records",
                   "parameters": {"entityName": "accounts", "$top": 1}},
        "connection": "Microsoft Dataverse",
    },
    "approvals_start_and_wait": {
        "name": "verify-approvals-start",
        "display_name": "验证 Approvals 发起审批",
        "action": {"connector": "approvals", "operation": "start_and_wait",
                   "parameters": {"approvalType": "Basic",
                                  "ApprovalCreationInput/title": "<占位：审批标题>",
                                  "ApprovalCreationInput/assignedTo": "<占位：审批人邮箱>",
                                  "ApprovalCreationInput/details": "<占位：审批说明>"}},
        "connection": "Approvals",
    },
    "forms_get_response_details": {
        "name": "verify-forms-response",
        "display_name": "验证 Forms 读回复",
        "action": {"connector": "forms", "operation": "get_response_details",
                   "parameters": {"form_id": "<占位：表单 ID>", "response_id": "<占位：回复 ID>"}},
        "connection": "Microsoft Forms",
    },
    "onedrive_get_file_content": {
        "name": "verify-onedrive-file",
        "display_name": "验证 OneDrive 读文件",
        "action": {"connector": "onedrive", "operation": "get_file_content",
                   "parameters": {"id": "<占位：文件路径>"}},
        "connection": "OneDrive for Business",
    },
    "planner_create_task": {
        "name": "verify-planner-create-task",
        "display_name": "验证 Planner 建任务",
        "action": {"connector": "planner", "operation": "create_task",
                   "parameters": {"group/groupId": "<占位：组 ID>",
                                  "plan/planId": "<占位：计划 ID>",
                                  "body/title": "<占位：任务标题>"}},
        "connection": "Planner",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 Power Automate operationId 验证包")
    parser.add_argument("--only", default="", help="只生成指定 case（key）")
    parser.add_argument("--workspace", default="workspace", help="工作区，默认 workspace")
    parser.add_argument("--out", default="pa-verify", help="输出子目录，默认 pa-verify")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    out_dir = args.out
    base = workspace / out_dir
    base.mkdir(parents=True, exist_ok=True)

    selected = [args.only] if args.only else sorted(CASES)
    missing = [k for k in selected if k not in CASES]
    if missing:
        parser.error(f"未知 case: {missing}; 可用: {sorted(CASES)}")

    rows: list[str] = []
    for key in selected:
        case = CASES[key]
        spec = {
            "name": case["name"],
            "display_name": case["display_name"],
            "trigger": {"type": "manual"},
            "actions": [{"name": "Step", **case["action"]}],
        }
        project = pa.write_flow_project(workspace, spec, output_dir=out_dir)
        pkg = pa.build_legacy_package(workspace, spec, output_dir=out_dir)
        report = pa.validate_project(workspace, project["name"], output_dir=out_dir)
        status = "OK" if report["ok"] else f"FAIL: {report['problems']}"
        rows.append(f"| `{key}` | `{pkg.name}` | {case['connection']} | {status} |")
        print(f"{key}: {pkg}")

    checklist = [
        "# Power Automate operationId 验证清单",
        "",
        "对每个包：make.powerautomate.com → 我的流 → 导入 → 导入包（旧版），",
        "选择对应连接，Flow 选 Create as new，点 Import。",
        "",
        "| case | 包 | 需要的连接 | 本地校验 |",
        "|---|---|---|---|",
        *rows,
        "",
        "导入结果记录到 `docs/POWER_AUTOMATE_VERIFIED.md`。",
    ]
    (base / "CHECKLIST.md").write_text("\n".join(checklist) + "\n", encoding="utf-8")
    print(f"清单: {base / 'CHECKLIST.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
