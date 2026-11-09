# Power Automate operationId 实测状态

> 目的：区分“文档值”与“在 示例租户真实导入成功”的 operationId。
> 新增连接器后，用 `scripts/power_automate_verify.py` 生成验证包，
> 导入成功后把对应行改为 ✅ 并记录日期。

## 状态

| 连接器 | operation | operationId | 状态 | 验证方式/日期 |
|---|---|---|---|---|
| Microsoft Teams | `post_message` | `PostMessageToConversation` | ✅ 已实测 | 2026-11-08，旧版包导入成功，My flows 创建 “示例 Teams 通知” |
| Microsoft Teams | `list_teams` | `GetAllTeams` | ⏳ 待实测 | 官方文档/公开实现 |
| Microsoft Teams | `get_team` | `GetTeam` | ⏳ 待实测 | 官方文档/公开实现 |
| Microsoft Teams | `list_channels` | `GetChannelsForGroup` | ⏳ 待实测 | 官方文档/公开实现 |
| Microsoft Teams | `list_channel_messages` | `GetMessagesFromChannel` | ⏳ 待实测 | 官方文档/公开实现 |
| Microsoft Teams | `list_chats` | `GetChats` | ⏳ 待实测 | 官方文档/公开实现 |
| Microsoft Teams | `list_members` | `ListMembers` | ⏳ 待实测 | 官方文档/公开实现 |
| Office 365 Outlook | `send_email` | `SendEmailV2` | ❌ 无法实测 | 2026-11-08：Connections 页面没有 Office 365 Outlook 连接，导入页连接列表为 “No items”；需先新建连接 |
| SharePoint | `get_items` | `GetItems` | ⏳ 待人工完成 | 2026-11-08：包上传、Create as new 均通过；连接映射/Import 仍是手动步骤 |
| Excel Online | `list_rows` | `GetItems` | ⏳ 待人工完成 | 已有连接，待导入 `verify-excel-list-rows` |
| Office 365 Users | `get_profile` | `UserProfile_V2` | ⏳ 待人工完成 | 已有连接，待导入 `verify-office365users-profile` |
| Dataverse | `list_records` | `ListRecords` | ⏳ 无连接 | 需先在 Connections 新建 Dataverse 连接 |
| Approvals | `start_and_wait` | `StartAndWaitForAnApproval` | ⏳ 无连接 | 需先新建连接 |
| Microsoft Forms | `get_response_details` | `GetResponseDetails` | ⏳ 无连接 | 需先新建连接 |
| OneDrive | `get_file_content` | `GetFileContent` | ⏳ 无连接 | 需先新建连接 |
| Planner | `create_task` | `CreateTask` | ⏳ 无连接 | 需先新建连接 |

## 怎么跑

```powershell
# 生成全部验证包 + 清单
python scripts/power_automate_verify.py

# 只生成一个
python scripts/power_automate_verify.py --only outlook_send_email
```

产物在 `workspace/pa-verify/`：

```
pa-verify/
├── CHECKLIST.md
├── verify-outlook-send-email/
├── verify-outlook-send-email-package.zip
├── verify-sharepoint-get-items/
...
```

导入：make.powerautomate.com → 我的流 → 导入 → 导入包（旧版），
选择对应连接，Flow 选 **Create as new**，点 **Import**。

## 判定标准

- **成功**：提示 “All package resources were successfully imported.” 且 My flows 出现对应流。
- **operationId 错误**：导入详情里出现
  `WorkflowOperationInputsApiOperationNotFound` / `The API operation '...' could not be found`。
- **连接问题**：连接下拉无可选项或提示 Reconnect → 先在 Connections 里重连。

## 记录模板

| 连接器 | operation | 结果 | 日期 | 备注 |
|---|---|---|---|---|
| ... | ... | ✅/❌ | ... | 错误码/消息 |
