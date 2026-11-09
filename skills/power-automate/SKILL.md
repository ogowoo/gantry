---
name: power-automate
description: 生成 Power Automate 工程的标准做法——同租户 automate 环境、spec/连接器目录、definition.json、旧版导入包与解决方案 zip
---

# 生成 Power Automate 工程

## 什么时候用

用户要一个 Power Automate（云端流）工程/自动化方案时。目标环境是与 Copilot **同租户**的
Power Automate（Maker 门户 `https://make.powerautomate.com`），即用户登录 Copilot 所用的
Microsoft 365 租户里默认可用的 automate 环境与内置连接器。

**边界**：本仓库没有 Power Platform 凭据，**不要调用任何真实 Power Automate API**。
只生成工程文件/导入包；导入由用户在 make.powerautomate.com 里手动完成。

## 两种用法

### 用法 A：直接用工具生成（推荐）

工具（由 `gantry/tool_plugins/power_automate.py` 提供）：

| 工具 | 作用 |
|---|---|
| `power_automate_catalog` | 查内置连接器 / 操作 / 控制动作 |
| `power_automate_generate` | 按 spec 生成工程 + 旧版导入包 + 解决方案 zip |
| `power_automate_validate` | 校验工程 JSON 与必填键 |

生成示例：

```json
{
  "name": "daily-report",
  "display_name": "每日报表提醒",
  "trigger": {"type": "recurrence", "frequency": "Day", "interval": 1,
              "timeZone": "China Standard Time", "startTime": "2026-01-01T09:00:00Z"},
  "actions": [
    {"name": "读取列表", "connector": "sharepoint", "operation": "get_items",
     "parameters": {"dataset": "https://contoso.sharepoint.com/sites/Ops",
                    "table": "日报"}},
    {"name": "发邮件", "connector": "outlook", "operation": "send_email",
     "parameters": {"emailMessage/To": "ops@contoso.com",
                    "emailMessage/Subject": "今日日报",
                    "emailMessage/Body": "请查收"}}
  ]
}
```

调用：

```
power_automate_generate(
  name="daily-report",
  spec=<上面的对象或 JSON 字符串>,
  target="all"          # project / legacy / solution / all
)
```

产出：

```
flows/daily-report/
├── flow/definition.json
├── connections.json
├── apis.json
└── README.md
flows/daily-report-package.zip    # 旧版导入包（最容易导入）
flows/daily-report-solution.zip   # 解决方案包
```

### 用法 B：脚本生成

```powershell
python scripts/power_automate_gen.py spec.json --workspace workspace --target all
```

## 交付目录

标准结构：`flows/<flow-name>/`（工具默认写到 `flows/<name>/`），另在 `flows/` 下生成
`<name>-package.zip` 与 `<name>-solution.zip`。

## spec 结构

| 键 | 说明 |
|---|---|
| `name` | 工程名（小写短横线），必填 |
| `display_name` | 流显示名，默认同 `name` |
| `description` | 说明，可选 |
| `trigger` | `{"type":"manual",...}` 或 `{"type":"recurrence","frequency":"Day","interval":1,...}` |
| `actions` | 动作数组，见下 |

动作两类：

1. **连接器动作**：`{"name":"...", "connector":"teams", "operation":"post_message", "parameters":{...}, "runAfter":["上一步"]}`
   - `runAfter` 省略时按数组顺序串行；
   - `connector`/`operation` 可用 `power_automate_catalog` 查。
2. **控制动作**：`{"name":"...", "type":"compose|delay|condition|foreach|scope|filter_array|initialize_variable|set_variable|append_to_string_variable|terminate|response|http", ...}`

## 同租户内置连接器

`power_automate_catalog` 会返回完整目录，常用：

| 服务 | key | 引用名 | 常用操作 |
|---|---|---|---|
| Office 365 Outlook | `outlook` | `shared_office365` | `send_email`（SendEmailV2）、`send_email_with_options` |
| SharePoint | `sharepoint` | `shared_sharepointonline` | `get_items`、`create_item`、`update_item`、`get_file_content` |
| Microsoft Teams | `teams` | `shared_teams` | `post_message`（PostMessageToConversation）、`list_teams`、`list_channels`、`list_channel_messages`、`list_chats`、`list_members` |
| Dataverse | `dataverse` | `shared_commondataserviceforapps` | `list_records`、`add_row`、`update_row`、`get_row` |
| Approvals | `approvals` | `shared_approvals` | `start_and_wait` |
| Microsoft Forms | `forms` | `shared_microsoftforms` | `get_response_details` |
| Excel Online | `excel` | `shared_excelonlinebusiness` | `list_rows`、`add_row` |
| OneDrive | `onedrive` | `shared_onedriveforbusiness` | `create_file`、`get_file_content` |
| Planner | `planner` | `shared_planner` | `create_task`、`update_task` |
| Office 365 Users | `office365users` | `shared_office365users` | `get_profile`、`search_users` |

## definition.json 的结构

顶层必含 `triggers` 与 `actions`，完整键：

| 键 | 说明 |
|---|---|
| `$schema` | 固定 `https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#` |
| `contentVersion` | `"1.0.0.0"` |
| `parameters` | `$connections`（Object）与 `$authentication`（SecureObject） |
| `triggers` | 手动 `Request` + `kind: Button`；计划 `Recurrence` |
| `actions` | 状态机 JSON；连接器动作为 `OpenApiConnection`，控制动作按 `type` |

## 导入方式

1. **旧版导入包（推荐）**：make.powerautomate.com → 我的流 → 导入 → 导入包（旧版），
   选 `*-package.zip`，按提示映射连接。
2. **解决方案**：make.powerautomate.com → 解决方案 → 导入解决方案，选 `*-solution.zip`；
   发布者/前缀不匹配时按租户调整（`publisher` / `prefix` 参数）。
3. **手工粘贴**：把 `flow/definition.json` 粘到「代码视图」。

## 校验（必做）

生成后工具会自动跑 `power_automate_validate`；也可手工：

```python
import json, pathlib
for p in pathlib.Path("flows/<flow-name>").rglob("*.json"):
    json.load(open(p, encoding="utf-8"))
    print("ok", p)
```

解析失败先修再交付；README 里同时列出所有 `<占位：...>` 让用户一目了然。

## 模板与示例

- `templates/flow-definition.json`：手动触发 + Teams 发消息的最小流；
- `templates/connections.json`：连接引用模板；
- `templates/apis.json`：连接器 API 清单模板；
- `examples/teams-notify.json`：示例 Teams 通知流 spec；
- `examples/outlook-daily.json`：Outlook 每日邮件 spec；
- `examples/sharepoint-to-teams.json`：SharePoint 列表 → 示例 Teams 通知 spec；
- `examples/teams-by-name.json`：按团队名/频道名动态查 ID 再发消息（用 `GetAllTeams` +
  `GetChannelsForGroup` + `filter_array`，避免写死 Team/Channel ID）。

## 开发参考

维护/扩展连接器、包格式与常见导入错误见
[docs/POWER_AUTOMATE_DEV.md](../../docs/POWER_AUTOMATE_DEV.md)：
operationId 核对表、spec 约定、旧版包/解决方案包结构、错误对照。
