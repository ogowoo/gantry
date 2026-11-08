# -*- coding: utf-8 -*-
"""Power Automate 工程生成库。

只生成**可导入的工程文件/包**，不调用任何 Power Platform API。
目标环境：与 Copilot 同租户的 Power Automate（make.powerautomate.com）。

对外主入口：
- `CONNECTORS` / `CONTROL_ACTIONS`：同租户内置连接器与常用操作目录；
- `build_definition(spec)`：生成 flow definition（Logic Apps 工作流定义）；
- `write_flow_project(root, spec)`：写 `flows/<name>/` 工程目录；
- `build_legacy_package(root, spec)`：生成「导入包（旧版）」zip；
- `build_solution_package(root, spec)`：生成解决方案 zip（Solutions → 导入）。
"""
from __future__ import annotations

import copy
import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = ("https://schema.management.azure.com/providers/Microsoft.Logic/schemas/"
          "2016-06-01/workflowdefinition.json#")
LOGIC_FLOWS_API = "/providers/Microsoft.PowerApps/apis/shared_logicflows"
APIS_PREFIX = "/providers/Microsoft.PowerApps/apis/"

# ── 同租户内置连接器目录 ───────────────────────────────────────────────
# 只收录常用、operationId 稳定的操作；parameters 给的是可直接导入的示例值，
# 生成时用 spec 里的 parameters 覆盖。
CONNECTORS: dict[str, dict[str, Any]] = {
    "teams": {
        "display_name": "Microsoft Teams",
        "ref": "shared_teams",
        "api_id": APIS_PREFIX + "shared_teams",
        "operations": {
            "post_message": {
                "operation_id": "PostMessageToConversation",
                "summary": "在 Teams 频道或群聊发消息",
                "parameters": {
                    "poster": "Flow bot",
                    "location": "Channel",
                    "body/recipient/groupId": "<占位：Team ID>",
                    "body/recipient/channelId": "<占位：Channel ID>",
                    "body/messageBody": "<占位：消息正文>",
                },
            },
            "list_teams": {
                "operation_id": "GetAllTeams",
                "summary": "列出当前用户加入的 Teams 团队",
                "parameters": {},
            },
            "get_team": {
                "operation_id": "GetTeam",
                "summary": "按 ID 读取团队信息",
                "parameters": {"teamId": "<占位：Team ID>"},
            },
            "list_channels": {
                "operation_id": "GetChannelsForGroup",
                "summary": "列出某个团队的频道",
                "parameters": {"groupId": "<占位：Team ID>"},
            },
            "list_channel_messages": {
                "operation_id": "GetMessagesFromChannel",
                "summary": "读取频道消息",
                "parameters": {"groupId": "<占位：Team ID>", "channelId": "<占位：Channel ID>"},
            },
            "list_chats": {
                "operation_id": "GetChats",
                "summary": "列出最近聊天",
                "parameters": {"chatType": "all", "topic": "all"},
            },
            "list_members": {
                "operation_id": "ListMembers",
                "summary": "列出群聊成员",
                "parameters": {"threadType": "groupchat", "body/recipient": "<占位：Chat ID>"},
            },
        },
    },
    "outlook": {
        "display_name": "Office 365 Outlook",
        "ref": "shared_office365",
        "api_id": APIS_PREFIX + "shared_office365",
        "operations": {
            "send_email": {
                "operation_id": "SendEmailV2",
                "summary": "发送邮件",
                "parameters": {
                    "emailMessage/To": "<占位：收件人>",
                    "emailMessage/Subject": "<占位：主题>",
                    "emailMessage/Body": "<占位：正文>",
                },
            },
            "send_email_with_options": {
                "operation_id": "SendEmailWithOptions",
                "summary": "发送带选项的邮件并等待回复",
                "parameters": {
                    "emailMessage/To": "<占位：收件人>",
                    "emailMessage/Subject": "<占位：主题>",
                    "emailMessage/Body": "<占位：正文>",
                    "emailMessage/Options": "Approve,Reject",
                },
            },
        },
    },
    "sharepoint": {
        "display_name": "SharePoint",
        "ref": "shared_sharepointonline",
        "api_id": APIS_PREFIX + "shared_sharepointonline",
        "operations": {
            "get_items": {
                "operation_id": "GetItems",
                "summary": "读取 SharePoint 列表项",
                "parameters": {
                    "dataset": "https://<租户>.sharepoint.com/sites/<站点>",
                    "table": "<占位：列表名>",
                },
            },
            "create_item": {
                "operation_id": "CreateItem",
                "summary": "新建 SharePoint 列表项",
                "parameters": {
                    "dataset": "https://<租户>.sharepoint.com/sites/<站点>",
                    "table": "<占位：列表名>",
                    "item/Title": "<占位：标题>",
                },
            },
            "update_item": {
                "operation_id": "UpdateItem",
                "summary": "更新 SharePoint 列表项",
                "parameters": {
                    "dataset": "https://<租户>.sharepoint.com/sites/<站点>",
                    "table": "<占位：列表名>",
                    "id": "<占位：列表项 ID>",
                },
            },
            "get_file_content": {
                "operation_id": "GetFileContent",
                "summary": "读取 SharePoint 文件内容",
                "parameters": {
                    "dataset": "https://<租户>.sharepoint.com/sites/<站点>",
                    "id": "<占位：文件路径>",
                },
            },
        },
    },
    "dataverse": {
        "display_name": "Microsoft Dataverse",
        "ref": "shared_commondataserviceforapps",
        "api_id": APIS_PREFIX + "shared_commondataserviceforapps",
        "operations": {
            "list_records": {
                "operation_id": "ListRecords",
                "summary": "查询 Dataverse 记录",
                "parameters": {"entityName": "accounts", "$select": "name", "$top": 10},
            },
            "add_row": {
                "operation_id": "AddRow",
                "summary": "新增 Dataverse 记录",
                "parameters": {"entityName": "accounts", "item/name": "<占位：名称>"},
            },
            "update_row": {
                "operation_id": "UpdateRow",
                "summary": "更新 Dataverse 记录",
                "parameters": {"entityName": "accounts", "recordId": "<占位：记录 ID>"},
            },
            "get_row": {
                "operation_id": "GetRow",
                "summary": "按 ID 读取 Dataverse 记录",
                "parameters": {"entityName": "accounts", "recordId": "<占位：记录 ID>"},
            },
        },
    },
    "approvals": {
        "display_name": "Approvals",
        "ref": "shared_approvals",
        "api_id": APIS_PREFIX + "shared_approvals",
        "operations": {
            "start_and_wait": {
                "operation_id": "StartAndWaitForAnApproval",
                "summary": "发起审批并等待结果",
                "parameters": {
                    "approvalType": "Basic",
                    "ApprovalCreationInput/title": "<占位：审批标题>",
                    "ApprovalCreationInput/assignedTo": "<占位：审批人邮箱>",
                    "ApprovalCreationInput/details": "<占位：审批说明>",
                },
            },
        },
    },
    "forms": {
        "display_name": "Microsoft Forms",
        "ref": "shared_microsoftforms",
        "api_id": APIS_PREFIX + "shared_microsoftforms",
        "operations": {
            "get_response_details": {
                "operation_id": "GetResponseDetails",
                "summary": "读取 Forms 回复明细",
                "parameters": {"form_id": "<占位：表单 ID>", "response_id": "<占位：回复 ID>"},
            },
        },
    },
    "excel": {
        "display_name": "Excel Online (Business)",
        "ref": "shared_excelonlinebusiness",
        "api_id": APIS_PREFIX + "shared_excelonlinebusiness",
        "operations": {
            "list_rows": {
                "operation_id": "GetItems",
                "summary": "读取 Excel 表格行",
                "parameters": {
                    "source": "<占位：文档库>",
                    "drive": "<占位：驱动器>",
                    "file": "<占位：工作簿>",
                    "table": "<占位：表名>",
                },
            },
            "add_row": {
                "operation_id": "AddRow",
                "summary": "向 Excel 表格追加行",
                "parameters": {
                    "source": "<占位：文档库>",
                    "drive": "<占位：驱动器>",
                    "file": "<占位：工作簿>",
                    "table": "<占位：表名>",
                },
            },
        },
    },
    "onedrive": {
        "display_name": "OneDrive for Business",
        "ref": "shared_onedriveforbusiness",
        "api_id": APIS_PREFIX + "shared_onedriveforbusiness",
        "operations": {
            "create_file": {
                "operation_id": "CreateFile",
                "summary": "在 OneDrive 新建文件",
                "parameters": {"folderPath": "<占位：目录>", "name": "<占位：文件名>"},
            },
            "get_file_content": {
                "operation_id": "GetFileContent",
                "summary": "读取 OneDrive 文件内容",
                "parameters": {"id": "<占位：文件路径>"},
            },
        },
    },
    "planner": {
        "display_name": "Planner",
        "ref": "shared_planner",
        "api_id": APIS_PREFIX + "shared_planner",
        "operations": {
            "create_task": {
                "operation_id": "CreateTask",
                "summary": "新建 Planner 任务",
                "parameters": {
                    "group/groupId": "<占位：组 ID>",
                    "plan/planId": "<占位：计划 ID>",
                    "body/title": "<占位：任务标题>",
                },
            },
            "update_task": {
                "operation_id": "UpdateTask",
                "summary": "更新 Planner 任务",
                "parameters": {"taskId": "<占位：任务 ID>"},
            },
        },
    },
    "office365users": {
        "display_name": "Office 365 Users",
        "ref": "shared_office365users",
        "api_id": APIS_PREFIX + "shared_office365users",
        "operations": {
            "get_profile": {
                "operation_id": "UserProfile_V2",
                "summary": "读取用户资料",
                "parameters": {"userPrincipalName": "<占位：用户 UPN>"},
            },
            "search_users": {
                "operation_id": "SearchUserV2",
                "summary": "搜索用户",
                "parameters": {"searchTerm": "<占位：搜索词>"},
            },
        },
    },
}

# 控制类动作（非连接器）
CONTROL_ACTIONS = {
    "compose": "组合",
    "delay": "等待",
    "condition": "条件",
    "foreach": "遍历",
    "scope": "范围",
    "initialize_variable": "初始化变量",
    "set_variable": "设置变量",
    "append_to_string_variable": "追加字符串变量",
    "terminate": "终止",
    "response": "响应",
    "http": "HTTP 请求",
}


class PowerAutomateError(ValueError):
    """生成/校验 Power Automate 工程时的可读错误。"""


def _safe_name(value: str, fallback: str = "flow", sep: str = "-") -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", sep, str(value or "").strip()).strip(sep)
    return text or fallback


def _unique(name: str, used: set[str]) -> str:
    base = name
    n = 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _connector(key: str) -> dict[str, Any]:
    k = str(key or "").strip().lower()
    if k not in CONNECTORS:
        names = "、".join(sorted(CONNECTORS))
        raise PowerAutomateError(f"不支持的连接器: {key}。可用：{names}")
    return CONNECTORS[k]


def _operation(connector: dict[str, Any], key: str) -> dict[str, Any]:
    k = str(key or "").strip().lower()
    ops = connector["operations"]
    if k not in ops:
        names = "、".join(sorted(ops))
        raise PowerAutomateError(f"连接器 {connector['display_name']} 不支持操作 {key}。可用：{names}")
    return ops[k]


def _parameters(operation: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    params = dict(operation.get("parameters") or {})
    for k, v in (overrides or {}).items():
        params[str(k)] = v
    return params


def _build_action(item: dict[str, Any], used: set[str]) -> dict[str, Any]:
    """把 spec 里的一个动作转成 Logic Apps 动作节点（不含 runAfter）。"""
    if "connector" in item:
        connector = _connector(item.get("connector"))
        operation = _operation(connector, item.get("operation"))
        ref = str(item.get("connection_name") or connector["ref"])
        return {
            "type": "OpenApiConnection",
            "inputs": {
                "host": {
                    "connectionName": ref,
                    "operationId": operation["operation_id"],
                    "apiId": connector["api_id"],
                },
                "parameters": _parameters(operation, item.get("parameters")),
                "authentication": "@parameters('$authentication')",
            },
        }

    kind = str(item.get("type") or "compose").strip().lower()
    if kind == "compose":
        return {"type": "Compose", "inputs": item.get("inputs", "")}
    if kind == "delay":
        try:
            count = max(1, int(item.get("count", 5)))
        except (TypeError, ValueError):
            raise PowerAutomateError("delay.count 必须是整数")
        unit = str(item.get("unit") or "Minute")
        return {"type": "Wait", "inputs": {"interval": {"count": count, "unit": unit}}}
    if kind == "condition":
        return {
            "type": "If",
            "expression": item.get("expression") or {"equals": ["@parameters('x')", ""]},
            "actions": _build_actions(item.get("actions") or [], used),
            "else": {"actions": _build_actions(item.get("else") or [], used)},
        }
    if kind == "foreach":
        return {
            "type": "Foreach",
            "foreach": item.get("items", "@triggerBody()"),
            "actions": _build_actions(item.get("actions") or [], used),
        }
    if kind == "scope":
        return {"type": "Scope", "actions": _build_actions(item.get("actions") or [], used)}
    if kind == "initialize_variable":
        return {
            "type": "InitializeVariable",
            "inputs": {"variables": [{
                "name": str(item.get("name") or "var"),
                "type": str(item.get("var_type") or "string").lower(),
                "value": item.get("value", ""),
            }]},
        }
    if kind == "set_variable":
        return {"type": "SetVariable",
                "inputs": {"name": str(item.get("name") or "var"), "value": item.get("value", "")}}
    if kind == "append_to_string_variable":
        return {"type": "AppendToStringVariable",
                "inputs": {"name": str(item.get("name") or "var"), "value": item.get("value", "")}}
    if kind == "terminate":
        return {"type": "Terminate",
                "inputs": {"runStatus": str(item.get("status") or "Succeeded"),
                           "runError": item.get("error", "")}}
    if kind == "response":
        return {"type": "Response",
                "inputs": {"statusCode": int(item.get("status_code", 200)),
                           "body": item.get("body", ""),
                           "headers": item.get("headers") or {}}}
    if kind == "http":
        return {"type": "Http",
                "inputs": {"method": str(item.get("method") or "GET"),
                           "uri": item.get("uri", ""),
                           "headers": item.get("headers") or {},
                           "body": item.get("body", "")}}
    names = "、".join(sorted(CONTROL_ACTIONS))
    raise PowerAutomateError(f"不支持的动作类型: {kind}。可用控制动作：{names}")


def _build_actions(items: list[dict[str, Any]], used: set[str]) -> dict[str, Any]:
    actions: dict[str, Any] = {}
    prev: str | None = None
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise PowerAutomateError(f"第 {idx} 个动作不是对象")
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            if "connector" in item:
                raw_name = f"{item.get('connector')}_{item.get('operation')}"
            else:
                raw_name = f"{item.get('type') or 'action'}_{idx}"
        name = _unique(_safe_name(raw_name, f"action_{idx}", sep="_"), used)
        action = _build_action(item, used)
        run_after = item.get("runAfter")
        if isinstance(run_after, dict) and run_after:
            action["runAfter"] = {str(k): list(v) if isinstance(v, list) else [str(v)]
                                  for k, v in run_after.items()}
        elif isinstance(run_after, list) and run_after:
            action["runAfter"] = {str(x): ["Succeeded"] for x in run_after}
        elif prev:
            action["runAfter"] = {prev: ["Succeeded"]}
        else:
            action["runAfter"] = {}
        actions[name] = action
        prev = name
    return actions


def _build_trigger(trigger: dict[str, Any] | None) -> dict[str, Any]:
    t = dict(trigger or {"type": "manual"})
    kind = str(t.get("type") or "manual").strip().lower()
    if kind in ("manual", "request", "button"):
        schema = t.get("inputs", {}).get("schema") if isinstance(t.get("inputs"), dict) else None
        if not schema:
            props = t.get("inputs") if isinstance(t.get("inputs"), dict) else {}
            if props and "schema" not in props:
                schema = {"type": "object", "properties": props, "required": []}
            else:
                schema = {"type": "object", "properties": {}, "required": []}
        return {"type": "Request", "kind": "Button", "inputs": {"schema": schema}}
    if kind == "recurrence":
        interval = t.get("interval", 1)
        try:
            interval = max(1, int(interval))
        except (TypeError, ValueError):
            raise PowerAutomateError("recurrence.interval 必须是整数")
        recurrence: dict[str, Any] = {
            "frequency": str(t.get("frequency") or "Day"),
            "interval": interval,
        }
        for key in ("startTime", "timeZone"):
            if t.get(key):
                recurrence[key] = t[key]
        for key in ("hours", "minutes"):
            if t.get(key):
                recurrence[key] = t[key]
        return {"type": "Recurrence", "recurrence": recurrence}
    raise PowerAutomateError("trigger.type 只能是 manual 或 recurrence")


def normalize_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """校验并补默认值；返回可直接用于生成的 spec。"""
    if not isinstance(spec, dict):
        raise PowerAutomateError("spec 必须是对象")
    name = _safe_name(spec.get("name") or "", "flow")
    actions = spec.get("actions")
    if not isinstance(actions, list) or not actions:
        raise PowerAutomateError("spec.actions 不能为空")
    out = copy.deepcopy(spec)
    out["name"] = name
    out["display_name"] = str(spec.get("display_name") or name)
    out["description"] = str(spec.get("description") or "")
    out["trigger"] = _build_trigger(spec.get("trigger"))
    return out


def build_definition(spec: dict[str, Any]) -> dict[str, Any]:
    """生成 workflow definition（含 triggers/actions）。"""
    normalized = normalize_spec(spec)
    used: set[str] = set()
    actions = _build_actions(normalized["actions"], used)
    return {
        "$schema": SCHEMA,
        "contentVersion": "1.0.0.0",
        "parameters": {
            "$connections": {"defaultValue": {}, "type": "Object"},
            "$authentication": {"defaultValue": {}, "type": "SecureObject"},
        },
        "triggers": {"manual": normalized["trigger"]},
        "actions": actions,
    }


def _used_connectors(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """遍历 definition 收集用到的连接器引用（去重，保持首次出现顺序）。"""
    found: dict[str, dict[str, Any]] = {}

    def walk(actions: Any) -> None:
        if not isinstance(actions, dict):
            return
        for _, action in actions.items():
            if not isinstance(action, dict):
                continue
            if action.get("type") in ("OpenApiConnection", "OpenApiConnectionWebhook"):
                host = (action.get("inputs") or {}).get("host") or {}
                ref = str(host.get("connectionName") or "")
                api_id = str(host.get("apiId") or "")
                if ref and ref not in found:
                    conn = next((c for c in CONNECTORS.values() if c["api_id"] == api_id), None)
                    found[ref] = {
                        "ref": ref,
                        "api_id": api_id,
                        "display_name": (conn or {}).get("display_name", ref),
                        "api_name": api_id.rsplit("/", 1)[-1] if api_id else ref,
                    }
            for key in ("actions",):
                walk(action.get(key))
            if isinstance(action.get("else"), dict):
                walk(action["else"].get("actions"))

    walk(definition.get("actions") or {})
    return list(found.values())


def build_connection_references(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        c["ref"]: {
            "runtimeSource": "embedded",
            "connection": {},
            "api": {"name": c["api_name"]},
        }
        for c in _used_connectors(definition)
    }


def build_connections_json(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        c["ref"]: {
            "id": c["api_id"],
            "displayName": c["display_name"],
            "connectionRef": f"<占位：连接引用逻辑名，如 new_{c['ref']}_xxxxx>",
            "connectionId": "<占位：同租户 automate 环境中的连接实例 ID>",
            "tier": "Standard",
        }
        for c in _used_connectors(definition)
    }


def build_apis_json(definition: dict[str, Any]) -> dict[str, Any]:
    return {
        c["ref"]: {
            "displayName": c["display_name"],
            "apiId": c["api_id"],
            "apiName": c["api_name"],
        }
        for c in _used_connectors(definition)
    }


def build_readme(spec: dict[str, Any], definition: dict[str, Any],
                 packages: dict[str, str] | None = None) -> str:
    name = spec.get("name") or "flow"
    display = spec.get("display_name") or name
    used = _used_connectors(definition)
    lines = [
        f"# {display}",
        "",
        f"- 工程名：`{name}`",
        f"- 触发方式：`{spec.get('trigger', {}).get('type')}`",
        f"- 连接器：{'、'.join(c['display_name'] for c in used) or '（无）'}",
        "",
        "## 文件",
        "",
        "```",
        f"flows/{name}/",
        "├── flow/definition.json    # 流定义",
        "├── connections.json        # 连接引用",
        "├── apis.json               # 连接器 API 清单",
        "└── README.md               # 本文件",
        "```",
        "",
        "## 导入方式",
        "",
        "### 方式 A：导入包（推荐，最直接）",
        "",
        "1. 打开 https://make.powerautomate.com → 我的流 → 导入 → 导入包（旧版）；",
        f"2. 选择 `{packages.get('legacy', name + '-package.zip') if packages else name + '-package.zip'}`；",
        "3. 在连接映射步骤为每个连接器选择同租户下已有连接，或新建连接；",
        "4. 导入后打开流并替换所有 `<占位：...>`（Team/Channel ID、邮箱、站点地址等）。",
        "",
        "### 方式 B：解决方案",
        "",
        "1. https://make.powerautomate.com → 解决方案 → 导入解决方案；",
        f"2. 选择 `{packages.get('solution', name + '-solution.zip') if packages else name + '-solution.zip'}`；",
        "3. 若提示发布者/前缀不匹配，按租户已有发布者调整后重新导入。",
        "",
        "### 方式 C：手工粘贴定义",
        "",
        "打开 `flow/definition.json` 全文，在「我的流 → 新建 → 从空白流」里用代码视图粘贴；",
        "连接引用按 `connections.json` 里的 `id` 逐个选择同租户连接。",
        "",
        "## 占位符清单",
        "",
    ]
    placeholders = sorted(set(re.findall(r"<占位：[^>]*>", json.dumps(definition, ensure_ascii=False))))
    if placeholders:
        lines += [f"- `{p}`" for p in placeholders]
    else:
        lines.append("（无）")
    lines += ["", "## 校验", "", "```powershell",
              f"python -c \"import json,pathlib; "
              f"[json.load(open(p, encoding='utf-8')) for p in pathlib.Path('flows/{name}').rglob('*.json')]; "
              "print('ok')\"", "```", ""]
    return "\n".join(lines)


def write_flow_project(root: Path, spec: dict[str, Any],
                       snap: Any = None, output_dir: str = "flows") -> dict[str, Any]:
    """写 `<output_dir>/<name>/` 工程目录，返回文件清单。"""
    normalized = normalize_spec(spec)
    definition = build_definition(normalized)
    name = normalized["name"]
    base = Path(root) / output_dir / name
    (base / "flow").mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {
        "flow/definition.json": json.dumps(definition, ensure_ascii=False, indent=2) + "\n",
        "connections.json": json.dumps(build_connections_json(definition), ensure_ascii=False, indent=2) + "\n",
        "apis.json": json.dumps(build_apis_json(definition), ensure_ascii=False, indent=2) + "\n",
    }
    for rel, content in files.items():
        target = base / rel
        if callable(snap):
            snap(f"{output_dir}/{name}/{rel}", target)
        target.write_text(content, encoding="utf-8", newline="")
    readme = base / "README.md"
    if callable(snap):
        snap(f"{output_dir}/{name}/README.md", readme)
    readme.write_text(build_readme(normalized, definition), encoding="utf-8", newline="")
    return {"name": name, "dir": str(base), "files": sorted([*files, "README.md"]),
            "definition": definition}


# ── 旧版导入包（Import Package (Legacy)）────────────────────────────
def _legacy_manifest(display: str, package_guid: str, telemetry_guid: str,
                     conns: list[dict[str, Any]]) -> dict[str, Any]:
    created = "2026-01-01T00:00:00.0000000Z"
    resources: dict[str, Any] = {}
    flow_depends: list[str] = []
    for c in conns:
        api_guid = str(uuid.uuid4())
        conn_guid = str(uuid.uuid4())
        c["_api_guid"] = api_guid
        c["_conn_guid"] = conn_guid
        flow_depends += [api_guid, conn_guid]
        resources[api_guid] = {
            "id": c["api_id"],
            "name": c["ref"],
            "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing",
            "details": {"displayName": c["display_name"]},
            "configurableBy": "System",
            "hierarchy": "Child",
            "dependsOn": [],
        }
        resources[conn_guid] = {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing",
            "creationType": "Existing",
            "details": {"displayName": c["ref"]},
            "configurableBy": "User",
            "hierarchy": "Child",
            "dependsOn": [api_guid],
        }
    resources[package_guid] = {
        "type": "Microsoft.Flow/flows",
        "suggestedCreationType": "Update",
        "creationType": "Existing, New, Update",
        "details": {"displayName": display},
        "configurableBy": "User",
        "hierarchy": "Root",
        "dependsOn": flow_depends,
    }
    return {
        "schema": "1.0",
        "details": {
            "displayName": display,
            "description": "",
            "createdTime": created,
            "packageTelemetryId": telemetry_guid,
            "creator": "N/A",
            "sourceEnvironment": "",
        },
        "resources": resources,
    }


def _legacy_flow_envelope(definition: dict[str, Any], display: str, flow_guid: str,
                          connection_references: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": flow_guid,
        "id": f"/providers/Microsoft.Flow/flows/{flow_guid}",
        "type": "Microsoft.Flow/flows",
        "properties": {
            "apiId": LOGIC_FLOWS_API,
            "displayName": display,
            "definition": definition,
            "connectionReferences": connection_references,
            "flowFailureAlertSubscribed": False,
            "isManaged": False,
        },
    }


def _fix_connector_inputs(actions: Any) -> None:
    """按 PA 导入器要求修正连接器动作：
    - 删除 inputs.authentication（导入会拒绝该属性）；
    - host 同时写 connectionName / connectionReferenceName；
    - 去掉现代 connection 字段。
    """
    if not isinstance(actions, dict):
        return
    for _, action in actions.items():
        if not isinstance(action, dict):
            continue
        kind = str(action.get("type") or "")
        if kind in ("OpenApiConnection", "OpenApiConnectionWebhook"):
            inputs = action.get("inputs")
            if isinstance(inputs, dict):
                inputs.pop("authentication", None)
                host = inputs.get("host")
                if isinstance(host, dict):
                    ref = host.get("connectionName") or host.get("connectionReferenceName") or host.get("connection")
                    if ref:
                        host.pop("connection", None)
                        host["connectionName"] = ref
                        host["connectionReferenceName"] = ref
        for key in ("actions",):
            _fix_connector_inputs(action.get(key))
        else_actions = action.get("else")
        if isinstance(else_actions, dict):
            _fix_connector_inputs(else_actions.get("actions"))
        cases = action.get("cases")
        if isinstance(cases, dict):
            for case in cases.values():
                if isinstance(case, dict):
                    _fix_connector_inputs(case.get("actions"))
        default = action.get("default")
        if isinstance(default, dict):
            _fix_connector_inputs(default.get("actions"))


def build_legacy_package(root: Path, spec: dict[str, Any],
                         out_path: Path | None = None, snap: Any = None,
                         output_dir: str = "flows") -> Path:
    """生成「导入包（旧版）」zip，可直接在 make.powerautomate.com 导入。"""
    normalized = normalize_spec(spec)
    definition = build_definition(normalized)
    definition = copy.deepcopy(definition)
    _fix_connector_inputs(definition.get("actions"))
    name = normalized["name"]
    display = normalized["display_name"]
    conns = _used_connectors(definition)
    package_guid = str(uuid.uuid4())
    flow_guid = str(uuid.uuid4())
    telemetry_guid = str(uuid.uuid4())
    manifest = _legacy_manifest(display, package_guid, telemetry_guid, conns)
    envelope = _legacy_flow_envelope(
        definition, display, flow_guid, build_connection_references(definition))
    inner_manifest = {
        "packageSchemaVersion": "1.0",
        "flowAssets": {"assetPaths": [package_guid]},
    }
    apis_map = {c["ref"]: c["_api_guid"] for c in conns}
    conns_map = {c["ref"]: c["_conn_guid"] for c in conns}
    files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2),
        "Microsoft.Flow/flows/manifest.json": json.dumps(inner_manifest, ensure_ascii=False, indent=2),
        f"Microsoft.Flow/flows/{package_guid}/apisMap.json": json.dumps(apis_map, ensure_ascii=False, indent=2),
        f"Microsoft.Flow/flows/{package_guid}/connectionsMap.json": json.dumps(conns_map, ensure_ascii=False, indent=2),
        f"Microsoft.Flow/flows/{package_guid}/definition.json": json.dumps(envelope, ensure_ascii=False, indent=2),
    }
    target = Path(out_path) if out_path else (
        Path(root) / output_dir / f"{name}-package.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    if callable(snap):
        snap(f"{output_dir}/{target.name}", target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return target


# ── 解决方案包（Solutions → 导入）──────────────────────────────────
def _solution_xml(spec: dict[str, Any], flow_guid: str, publisher: str,
                  prefix: str, version: str) -> str:
    from xml.sax.saxutils import escape
    unique = _safe_name(f"{spec['name']}-solution", "solution")
    display = escape(str(spec.get("display_name") or spec["name"]))
    pub = escape(publisher)
    pref = escape(prefix)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml version="9.2.24052.179" SolutionPackageVersion="9.2" languagecode="1033" generatedBy="CrmLive" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <SolutionManifest>
    <UniqueName>{unique}</UniqueName>
    <LocalizedNames>
      <LocalizedName description="{display}" languagecode="1033" />
    </LocalizedNames>
    <Descriptions />
    <Version>{escape(version)}</Version>
    <Managed>0</Managed>
    <Publisher>
      <UniqueName>{pub}</UniqueName>
      <LocalizedNames>
        <LocalizedName description="{pub}" languagecode="1033" />
      </LocalizedNames>
      <Descriptions />
      <EMailAddress xsi:nil="true"></EMailAddress>
      <SupportingWebsiteUrl xsi:nil="true"></SupportingWebsiteUrl>
      <CustomizationPrefix>{pref}</CustomizationPrefix>
      <CustomizationOptionValuePrefix>10000</CustomizationOptionValuePrefix>
    </Publisher>
    <RootComponents>
      <RootComponent type="29" id="{flow_guid}" behavior="0" />
    </RootComponents>
    <MissingDependencies />
  </SolutionManifest>
</ImportExportXml>
"""


def _customizations_xml(spec: dict[str, Any], flow_guid: str) -> str:
    from xml.sax.saxutils import escape
    schema_name = _safe_name(spec["name"], "flow")
    display = escape(str(spec.get("display_name") or schema_name))
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ImportExportXml xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Entities />
  <Workflows>
    <Workflow Name="{schema_name}" Id="{flow_guid}" Category="5" XamlFileName="" Type="1" Subprocess="0" IsTransacted="1" IsCustomizable="1" IntroducedVersion="1.0.0.0" IsCustomProcessingStep="0" Mode="0" Scope="4" TriggerOnCreate="0" TriggerOnDelete="0" TriggerOnUpdateAttributeList="" RunAsUser="0" PrimaryEntity="none" StatusCode="1" StateCode="0" IsManaged="0">
      <JsonFileName>Workflows/{schema_name}-{flow_guid}.json</JsonFileName>
      <Labels>
        <Label description="{display}" languagecode="1033" />
      </Labels>
    </Workflow>
  </Workflows>
</ImportExportXml>
"""


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/octet-stream" />
  <Default Extension="json" ContentType="application/octet-stream" />
</Types>
"""


def build_solution_package(root: Path, spec: dict[str, Any],
                           out_path: Path | None = None,
                           publisher: str = "GantryPublisher",
                           prefix: str = "new",
                           version: str = "1.0.0.0",
                           snap: Any = None,
                           output_dir: str = "flows") -> Path:
    """生成解决方案 zip（Solutions → 导入解决方案）。

    注意：solution.xml 的发布者/前缀需与目标租户匹配；导入前按 README 调整。
    """
    normalized = normalize_spec(spec)
    definition = build_definition(normalized)
    name = normalized["name"]
    flow_guid = str(uuid.uuid4())
    envelope = {
        "properties": {
            "apiId": LOGIC_FLOWS_API,
            "displayName": normalized["display_name"],
            "definition": definition,
            "connectionReferences": build_connection_references(definition),
            "flowFailureAlertSubscribed": False,
            "isManaged": False,
        },
        "schemaVersion": "1.0.0.0",
        "id": f"/providers/Microsoft.Flow/flows/{flow_guid}",
        "name": flow_guid,
        "type": "Microsoft.Flow/flows",
    }
    files = {
        "[Content_Types].xml": _content_types_xml(),
        "solution.xml": _solution_xml(normalized, flow_guid, publisher, prefix, version),
        "customizations.xml": _customizations_xml(normalized, flow_guid),
        f"Workflows/{_safe_name(name)}-{flow_guid}.json": json.dumps(envelope, ensure_ascii=False, indent=2),
    }
    target = Path(out_path) if out_path else (
        Path(root) / output_dir / f"{name}-solution.zip")
    target.parent.mkdir(parents=True, exist_ok=True)
    if callable(snap):
        snap(f"{output_dir}/{target.name}", target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in files.items():
            zf.writestr(rel, content)
    return target


# ── 校验 ──────────────────────────────────────────────────────────
def _connector_for_api(api_id: str) -> dict[str, Any] | None:
    for conn in CONNECTORS.values():
        if conn["api_id"] == api_id:
            return conn
    return None


def _walk_actions(actions: Any):
    if not isinstance(actions, dict):
        return
    for name, action in actions.items():
        if not isinstance(action, dict):
            continue
        yield str(name), action
        yield from _walk_actions(action.get("actions"))
        if isinstance(action.get("else"), dict):
            yield from _walk_actions(action["else"].get("actions"))
        if isinstance(action.get("cases"), dict):
            for case in action["cases"].values():
                if isinstance(case, dict):
                    yield from _walk_actions(case.get("actions"))
        if isinstance(action.get("default"), dict):
            yield from _walk_actions(action["default"].get("actions"))


def validate_definition(definition: dict[str, Any]) -> dict[str, Any]:
    """校验 definition 里的连接器操作与占位符；problems 是硬错误，warnings 是提醒。"""
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(definition, dict):
        return {"ok": False, "problems": ["definition 不是对象"], "warnings": []}
    for key in ("triggers", "actions", "$schema", "contentVersion"):
        if key not in definition:
            problems.append(f"definition 缺少 {key}")
    for name, action in _walk_actions(definition.get("actions") or {}):
        if action.get("type") not in ("OpenApiConnection", "OpenApiConnectionWebhook"):
            continue
        host = (action.get("inputs") or {}).get("host") or {}
        api_id = str(host.get("apiId") or "")
        op = str(host.get("operationId") or "")
        conn = _connector_for_api(api_id)
        if conn is None:
            warnings.append(f"动作 {name}: 未收录的 apiId {api_id}（本地无法校验 operationId）")
            continue
        known = {o["operation_id"] for o in conn["operations"].values()}
        if op not in known:
            problems.append(
                f"动作 {name}: operationId {op!r} 不在 {conn['display_name']} 的已知操作里"
                f"（可用：{', '.join(sorted(known))}）")
    placeholders = sorted(set(re.findall(r"<占位：[^>]*>", json.dumps(definition, ensure_ascii=False))))
    if placeholders:
        warnings.append(f"还有 {len(placeholders)} 个占位符待替换")
    return {"ok": not problems, "problems": problems, "warnings": warnings,
            "placeholders": placeholders}


def validate_project(root: Path, name: str, output_dir: str = "flows") -> dict[str, Any]:
    """校验工程目录里的 JSON 是否可解析、definition 必填键与连接器操作是否有效。"""
    base = Path(root) / output_dir / name
    problems: list[str] = []
    warnings: list[str] = []
    if not base.is_dir():
        raise PowerAutomateError(f"工程目录不存在: {base}")
    checked = []
    for p in sorted(base.rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            problems.append(f"{p.name}: JSON 解析失败: {exc}")
            continue
        checked.append(p.name)
        if p.name == "definition.json":
            report = validate_definition(data)
            problems.extend(report["problems"])
            warnings.extend(report["warnings"])
    return {"ok": not problems, "checked": checked, "problems": problems,
            "warnings": warnings}
