# Power Automate 开发指南

面向后续维护/扩展 `gantry/power_automate.py`、`gantry/power_automate_ui.py` 与
`skills/power-automate/` 的开发者。

## 1. 能力边界

- **只生成工程/导入包**：不调用 Power Platform API，不需要应用注册或管理员 consent。
- **导入**：两种包——旧版导入包（`*-package.zip`）与解决方案包（`*-solution.zip`）。
  旧版包最稳，导入路径：make.powerautomate.com → 我的流 → 导入 → 导入包（旧版）。
- **登录**：复用服务现有的持久化 `EdgeProfile`（与 Copilot/Teams 同租户 SSO）。
  `data/pa-auth.json` 保存 Power Platform 相关 cookie（已 gitignore），
  服务重启后由 `restore_auth_state()` 注入，不用反复登录。
- **最后一步**：连接选择器是自定义 Angular 控件，合成点击不可靠；自动化做到
  “打开导入页 → 上传包 → Flow 选 Create as new”，连接选择与最终 Import 目前建议人工点。

## 2. 代码地图

| 文件 | 职责 |
|---|---|
| `gantry/power_automate.py` | 连接器目录、spec → definition、工程写入、两种包打包、校验 |
| `gantry/power_automate_ui.py` | maker 门户 probe/login/dom/import 流程与 DOM 定位 |
| `gantry/tool_plugins/power_automate.py` | 三个 Agent 工具：catalog / generate / validate |
| `scripts/power_automate_gen.py` | 命令行生成器（CI/本地均可用） |
| `skills/power-automate/` | 技能说明、模板、示例 spec |

## 3. 连接器与 operationId（已核对的常用项）

> 实测状态：Teams `post_message`（`PostMessageToConversation`）已在 示例租户
> 成功导入并创建流；其余 operationId 来自官方连接器文档/公开实现，首次使用
> 建议先导入验证一次，确认无误后再沉淀到本表。

> 关键教训：`PostMessageV3` 已下线，导入报
> `WorkflowOperationInputsApiOperationNotFound`。加新操作时务必核对
> operationId，不要凭记忆写。

### Microsoft Teams（`shared_teams`）

| spec 操作 | operationId | 主要参数 |
|---|---|---|
| `post_message` | `PostMessageToConversation` | `poster`、`location`、`body/recipient/groupId`、`body/recipient/channelId`、`body/messageBody` |
| `list_teams` | `GetAllTeams` | 无 |
| `get_team` | `GetTeam` | `teamId` |
| `list_channels` | `GetChannelsForGroup` | `groupId` |
| `list_channel_messages` | `GetMessagesFromChannel` | `groupId`、`channelId` |
| `list_chats` | `GetChats` | `chatType`、`topic` |
| `list_members` | `ListMembers` | `threadType`、`body/recipient` |

### 其他连接器

| key | 引用名 | 已收录操作 |
|---|---|---|
| `outlook` | `shared_office365` | `send_email`（SendEmailV2）、`send_email_with_options` |
| `sharepoint` | `shared_sharepointonline` | `get_items`、`create_item`、`update_item`、`get_file_content` |
| `dataverse` | `shared_commondataserviceforapps` | `list_records`、`add_row`、`update_row`、`get_row` |
| `approvals` | `shared_approvals` | `start_and_wait` |
| `forms` | `shared_microsoftforms` | `get_response_details` |
| `excel` | `shared_excelonlinebusiness` | `list_rows`、`add_row` |
| `onedrive` | `shared_onedriveforbusiness` | `create_file`、`get_file_content` |
| `planner` | `shared_planner` | `create_task`、`update_task` |
| `office365users` | `shared_office365users` | `get_profile`、`search_users` |

控制动作：`compose`、`delay`、`condition`、`foreach`、`scope`、
`initialize_variable`、`set_variable`、`append_to_string_variable`、
`terminate`、`response`、`http`。

## 4. 新增/修改连接器操作

1. 在 `gantry/power_automate.py` 的 `CONNECTORS[...]["operations"]` 增加：
   ```python
   "list_teams": {
       "operation_id": "GetAllTeams",
       "summary": "列出当前用户加入的 Teams 团队",
       "parameters": {},
   },
   ```
2. 在 `tests/test_power_automate_tools.py` 增加用例，断言生成的
   `inputs.host.operationId` 与 `validate_definition()` 结果。
3. `python -m unittest tests.test_power_automate_tools`。
4. 需要示例时更新 `skills/power-automate/examples/*.json` 与 `SKILL.md` 的表格。
5. 重新生成一次包并**实测导入**（operationId 是否正确只有导入才能最终确认）。

## 5. spec 约定

```json
{
  "name": "daily-report",
  "display_name": "每日报表",
  "trigger": {"type": "recurrence", "frequency": "Day", "interval": 1,
              "timeZone": "China Standard Time", "startTime": "2026-01-01T09:00:00Z"},
  "actions": [
    {"name": "读取列表", "connector": "sharepoint", "operation": "get_items",
     "parameters": {"dataset": "https://contoso.sharepoint.com/sites/Ops", "table": "日报"}},
    {"name": "发邮件", "connector": "outlook", "operation": "send_email",
     "parameters": {"emailMessage/To": "ops@contoso.com",
                    "emailMessage/Subject": "今日日报",
                    "emailMessage/Body": "请查收"}}
  ]
}
```

- `runAfter` 省略时按数组顺序串行；显式给 `["上一步名字"]` 可改 DAG。
- 动作名会被规整为 `[A-Za-z0-9_]`；中文名可能变成只剩英文/数字，建议直接给英文名。
- 占位符统一用 `<占位：说明>`，README/校验会列出来。

## 6. 包格式

### 旧版导入包

```
manifest.json
Microsoft.Flow/flows/manifest.json
Microsoft.Flow/flows/<package-guid>/apisMap.json
Microsoft.Flow/flows/<package-guid>/connectionsMap.json
Microsoft.Flow/flows/<package-guid>/definition.json
```

关键修正（`build_legacy_package` 自动做）：

- 删除连接器动作的 `inputs.authentication`；
- `host` 同时写 `connectionName` 和 `connectionReferenceName`；
- 去掉现代 `host.connection` 字段。

### 解决方案包

```
[Content_Types].xml
solution.xml
customizations.xml
Workflows/<name>-<guid>.json
```

`solution.xml` 的发布者/前缀需与目标租户匹配；不匹配时用
`publisher` / `prefix` 参数调整后重新生成。

## 7. 常用命令

```powershell
# 生成 + 两种包
python scripts/power_automate_gen.py spec.json --workspace workspace --target all

# 校验工程（JSON + operationId + 占位符）
python -c "from gantry import power_automate as pa; print(pa.validate_project('.', 'daily-report'))"

# UI 探测（服务运行时）
curl -X POST http://127.0.0.1:8765/v1/power-automate/probe
```

## 8. 常见错误对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `WorkflowOperationInputsApiOperationNotFound` | operationId 写错/已下线 | 按本文表格核对，改 `CONNECTORS` 后重生成 |
| `Import conflicts` + “save as a new flow” | 通常是上面的保存失败连带提示 | 先修 operationId，再重试 |
| 连接下拉里没有可选连接 | 连接状态是 `Reconnect` | make.powerautomate.com → Connections → 重新连接 |
| 导入后运行失败 | 还有 `<占位：...>` 未替换 | 打开流替换 Team/Channel/邮箱等 |
| 登录反复失效 | 会话 cookie 未保存/被清 | 确认 `data/pa-auth.json` 存在且 `data/` 被 gitignore |

## 9. 安全

- 不把 `data/pa-auth.json`、`EdgeProfile/`、`config.json` 提交到仓库；
- 不在包/示例里写真实凭据、Team ID、邮箱等敏感值，用 `<占位：...>`；
- UI 自动化只操作本机已登录的租户页面，不调用未授权的 API。
