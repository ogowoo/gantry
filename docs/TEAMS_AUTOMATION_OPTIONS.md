# Teams 自动回复：已试方案、结果与受限点

> 目的：把这轮排查过的所有“定位 + 发送”方案列清楚，方便其它 AI/同事判断还有没有更稳的路。
> 当前环境：示例租户、`user@example.com`、Windows、本地 Gantry `127.0.0.1:8765`、Edge 持久化 profile、ADFS/MFA、无管理员授权、无 App 注册。
> 目标范围：**只处理 1:1 聊天；群聊 @ 不处理**。

## 0. 硬约束

1. 同租户、零管理员授权、零 App 注册（Bot Framework/Graph 应用权限基本排除）。
2. Gantry 跑在本机，**Power Automate 云端访问不到 `127.0.0.1`**，公司也没有公网入口/on-prem gateway。
3. Teams 登录是 ADFS + MFA，会话会过期；不能反复让用户登录。
4. 只允许回复白名单会话（当前 `Chen, Alex`），**绝不能发错会话**。
5. 群聊 @ 明确不处理。

## 1. 当前实现（本地浏览器自动化）

| 文件 | 职责 |
|---|---|
| `gantry/teams_send.py` | 定位会话 + 输入 + 点发送 |
| `gantry/teams_dom.py` | 页面原语、`_SEL` 选择器、聊天列表行点击 |
| `gantry/teams_api.py` | chatsvc API 读取/发送（可选） |
| `gantry/teams_mentions.py` | Chat → Mentions 读取与点行 |
| `plugins/teams-bot/plugin.py` | 轮询、去重、队列、outbox、配置 |

发送核心：`_compose_text_unlocked()` 找输入框 → `keyboard.type(delay=8)` 逐字输入 → 找发送按钮点击 → 校验输入框清空 → 失败再按 Enter。

定位顺序（`reply_to` / `_reply_to_unlocked`）：

1. 当前已停在目标会话（头部标题匹配）→ 直接输入；
2. 有 `conv_id` → 先试 chatsvc API；
3. 按 threadId 精确点击聊天列表行（`open_chat_by_thread_id`，读 `data-fui-tree-item-value`）；
4. 聊天列表行（`open_chat_by_row`）→ 片段优先，片段不匹配再按 peer；
5. 消息片段搜索（`api_open_by_snippet`）；
6. 人名搜索（`open_chat`）；
7. 最后校验头部标题（容忍 `姓名 - 公司` 后缀）。

## 2. 已试方案

### 2.1 IDB 读会话 + `lastId` 变化

- 读 Teams IndexedDB 的会话列表，按 `lastId` 变化判断新消息（即使聊天开着、被自动标已读也能发现）。
- 优点：能发现**已读**的新消息；不依赖未读 badge。
- 缺点：定位仍要回到 UI；群聊要过滤；DOM/IDB 结构随版本变。
- 结果：**读侧可用**，9:35 的 1:1 回复就是这条路径成功的。

### 2.2 聊天列表行点击（`open_chat_by_row`）

- 按 peer 文本找左侧会话行，点标题/行中心，读头部标题确认切换。
- 改进过：先回 Chat 首页、片段不匹配时按 peer 兜底、标题容忍 `姓名 - 公司` 后缀。
- 结果：**部分成功**（9:26、7:21、9:35 都发出去过）；仍会因页面停在搜索/Mentions、行文本差异、同名会话而失败。

### 2.3 消息片段 / 人名搜索

- 用最后一条消息片段搜索，命中聊天；或用人名搜索。
- 缺点：片段可能被 `<at>`/前缀/HTML 污染；人名会打开联系人名片；搜索结果页没有输入框。
- 结果：**不稳定**，只作为兜底。

### 2.4 深链 `/l/chat/<conv_id>/conversations`

- 官方深链格式。
- 实际结果：跳到 **Teams 桌面客户端启动器**（`dl/launcher/launcher.html`），弹 “This site is trying to open Microsoft Teams”，不是 Web 聊天窗口；发送 45s 超时。
- 也试过 SPA 形式：`/v2/#/chat/<id>`、`#/conversations/<id>`、`?threadId=<id>`，不稳定/超时；`api_open_by_url` 直接卡住。
- 结果：**不可用，已移除**。

### 2.5 Chat → Mentions 视图

- DOM：容器 `[data-tid="app-layout-area--sub-nav"]`，行 `[role="row"]`，行内有 `message-slice-card-title`（发送人）、`message-slice-card-preview`（消息）、`message-slice-card-timestamp`（时间）。
- 点行能打开对应会话；行里**没有 conversationId 属性**。
- 未读 badge 在左侧 `Mentions` 树项上，可做 gate。
- 致命问题：Mentions 默认 **Unread** 过滤，**当前聊天开着时新消息被自动标已读，badge=0、列表里也看不到**。实测 10:07 的“Outlook 打不开”就是这样漏掉的；关闭 gate 后机器人反而回了更早的未读 mention（9:26、7:21）。
- 群聊：`only_direct=true` 会跳过；`chats` 可同时匹配发送人和聊天标题；`chat_kind` 判断群聊。
- 结果：**只适合“别的会话未读 @我”**，不适合当前 1:1 已读消息。

### 2.6 chatsvc API（`gantry/teams_api.py`）

- 通过页面 hook 能拿到 `skypeToken`（`api/tokens-all` 显示 `skype_found: true`）。
- `GET /api/chatsvc/emea/.../conversations` 返回 15 个会话，token 有效。
- 目标 1:1 会话 `19:...@unq.gbl.spaces` **不在列表里**；按 id 直查 404。
- `api_send_message` 用 `X-Skypetoken` POST 到 chatsvc；因为目标 id 解析不到，只能回退 UI。
- `api_token()`（`POST /api/authsvc/v1.0/authz`）在新域名下报 `TypeError: Failed to fetch`；`_ensure_skype_token` 依赖已捕获的 token，热重载后 `_CAPTURED` 会丢。
- 结果：**聊天列表可读，1:1 不可发**；不是通用方案。

### 2.7 新版 mt REST API

- 页面实际请求：`/api/mt/part/emea-03/beta/users/<userId>/threads/<threadId>/properties/pictureV2`。
- 试过 `.../threads/<id>/messages`、`.../threads/<id>`、`.../threads?pageSize=5`，全部 **404**。
- 结论：Teams Web 的消息大概率走 WebSocket/IC3，不是这套 REST；没有简单的“按 threadId POST 消息”接口。

### 2.8 Power Automate 服务端方案

- Teams 连接器 **When a new chat message is added**：Webhook 近实时，覆盖 1:1、群聊、会议聊天；用 **Get message details** 拿 `mentions`/`body.content`/`from.user`；用 **Post message in a chat or channel** 按 `conversationId` 回复。
- 优点：不依赖 DOM、不怕已读、群聊也能处理；是理论上最稳的。
- **阻塞**：公司 flow 访问不到本地 Gantry，没有公网地址/on-prem gateway；用户明确说不可行。
- 其它触发器：`When a new message is added to a chat or channel`（限定单个会话）、`When keywords are mentioned`（只支持单关键词，`Alex Chen - Example` 是多词，不适合）。

### 2.9 Microsoft Graph API

- `GET /me/chats`、`GET /chats/{id}/messages`、`POST /chats/{id}/messages`，`chatMessage.mentions` 可判断 @。
- 优点：服务端、稳定；本地脚本拿到 token 就能用。
- 阻塞：需要 `Chat.Read`/`Chat.ReadWrite` delegated 权限，部分租户要管理员同意；目前没有从浏览器提取 Graph token，也没做应用注册。
- 结果：**未实现**，是潜在方案。

### 2.10 Bot Framework / Outgoing Webhook

- Bot Framework 要 App 注册 + 管理员授权 + 公网回调。
- Outgoing Webhook 要 @ 提及 + 公网 HTTPS + 团队级配置。
- 结果：**不符合零授权约束**。

### 2.11 IC3 / 内部 API 库

- 例如 `ic3-teams`、`teams-mcp` 这类库，直接用 Teams 内部协议/token 读写消息。
- 优点：能绕开 DOM。
- 缺点：逆向接口、token 易失效、随版本变、合规风险。
- 结果：**未采用**。

### 2.12 桌面客户端 UI 自动化

- 用 WinAppDriver / Power Automate Desktop 操作原生 Teams 客户端。
- 优点：本地、不依赖 Web DOM。
- 缺点：重、选择器也随版本变、需要桌面会话。
- 结果：**未试**。

## 3. 结果汇总

| 方案 | 读消息 | 定位 1:1 | 发送 | 群聊 | 结论 |
|---|---|---|---|---|---|
| IDB + lastId | ✅ 含已读 | ⚠️ 靠 UI | ⚠️ UI | 可过滤 | 当前主路径 |
| 聊天列表行点击 | - | ⚠️ 部分成功 | ✅ 成功过 | 可过滤 | 当前兜底/主定位 |
| 片段/人名搜索 | - | ⚠️ 不稳定 | ⚠️ | - | 兜底 |
| `/l/chat` 深链 | - | ❌ 启动器 | ❌ 超时 | - | 已移除 |
| Mentions 视图 | ⚠️ 仅未读 | ✅ 点行 | ✅ | ✅ | 只适合别的会话未读 |
| chatsvc API | ✅ 列表 | ❌ 1:1 不在列表 | ❌ | ⚠️ | 不可用 |
| mt REST API | ❌ 404 | ❌ | ❌ | - | 无消息接口 |
| Power Automate | ✅ | ✅ | ✅ | ✅ | 网络不通，阻塞 |
| Graph API | ✅ | ✅ | ✅ | ✅ | 权限/token 未解决 |
| Bot/Outgoing Webhook | ✅ | ✅ | ✅ | ✅ | 需管理员/公网 |
| IC3 内部库 | ✅ | ✅ | ✅ | ✅ | 逆向，风险 |
| 桌面 UI 自动化 | ✅ | ⚠️ | ⚠️ | ⚠️ | 未试，重 |

## 4. 受限点清单

1. **本地服务无法被云端 flow 访问**：Power Automate 方案直接死在这。
2. **1:1 会话不在 chatsvc 列表**：按 id 发不了；目标 id 直查 404。
3. **mt REST 没有 messages 接口**：Teams Web 消息走 WebSocket/IC3。
4. **深链会拉起桌面客户端启动器**：不能用于 Web 自动化。
5. **Mentions 只看 Unread**：当前聊天已读消息看不到。
6. **聊天列表行没有 conversationId 属性**：只能按文本/片段点，存在同名/排序问题。
7. **没有 Graph token**：Graph 方案未落地；`Chat.ReadWrite` 可能还要管理员同意。
8. **ADFS/MFA 会话会过期**：cookie 持久化只能缓解，不能根治。
9. **Teams DOM 随版本变**：`_SEL` 需要跟着改。
10. **群聊 @ 不处理**：`only_direct=true`，Mentions 里的群聊会跳过。

## 5. 给其它 AI 的问题

1. 本地脚本（无管理员、无 App 注册）有没有**受支持**的方式，按 1:1 `conversationId` 直接发 Teams 消息？Graph delegated 是否可行？需要什么权限/同意？
2. Teams Web 有没有**不触发桌面启动器**的 SPA 路由，能按 chat id 打开会话？例如 `/v2/?threadId=` 之外的形式？
3. 聊天列表行 DOM 里有没有隐藏的 conversationId 属性（React key、`data-*`、`id`）可用于精确点击？
4. 除了 chatsvc/mt REST，Teams Web 还有没有可复用的“按 threadId 发消息”的 HTTP 接口（含 WebSocket/IC3 的握手）？
5. 在“云端访问不到本地”的前提下，Power Automate 有没有 on-prem gateway / Desktop flow / 反向通道的可行做法？
6. 有没有本地优先、维护活跃的 Teams 自动化库，能稳定处理 1:1 的读/发，且不要求管理员授权？

## 6. 最新实测补充（2026-11-09 深夜）

### 6.1 UPN 深链也不可用

按建议测试：

```
https://teams.microsoft.com/l/chat/0/0?users=peer@example.com
```

结果仍然跳到桌面客户端启动器：

```
url: https://teams.microsoft.com/dl/launcher/launcher.html?...
title: Join conversation
body: Stay better connected with the Teams desktop app
```

点 “Use the web app instead” 也没有稳定进入聊天。结论：**UPN 深链同样不可用于 Web 自动化**。

### 6.2 左侧聊天行有精确 threadId 映射

实测左侧列表行的 `data-fui-tree-item-value` 属性里直接带 conversationId：

```
OneGQL_UserDefinedConversationFolder|<folder>/OneGQL_OneOnOneChatConversation|19:...@unq.gbl.spaces
```

已实现 `open_chat_by_thread_id()`：按这个属性精确匹配行，点行内 `span[title]`，再校验头部标题。  
取消折叠后，目标 `Chen, Alex` 实测成功：

```json
{
  "opened": true,
  "how": "thread-id",
  "chat": "Chen, Alex ...",
  "text": "Chen, Alex\n10:10 PM\nYou: 能收到..."
}
```

这是目前最稳的本地定位方式：**只要会话在渲染列表里，就按 id 点，不依赖文本/排序**。

### 6.3 Graph PowerShell 公共客户端被 Conditional Access 拦截

已安装 `Microsoft.Graph.Authentication 2.41.2`，用设备码登录：

```
https://login.microsoft.com/device
client id: 14d82eec-204b-4c2f-b7e8-296a70dab67e (Microsoft Graph Command Line Tools)
scope: Chat.ReadWrite
```

设备码输入后，示例登录页返回：

> You don't have access to this
> Your sign-in was successful but does not meet the criteria to access this resource...
> an authentication flow that is restricted by your admin.

结论：**示例租户的 Conditional Access 拦截了该公共客户端/设备码流程**。Graph 路线除非有已批准的应用注册，否则走不通。

### 6.4 最终建议

- 读：`IDB + lastId`（已能发现已读新消息）。
- 定位：`data-fui-tree-item-value` 的 threadId 精确点击 → 文本/片段兜底。
- 发送：UI 输入/点发送（已可用），发送前做 UPN/threadId/1:1 三重校验。
- 不碰：UPN 深链、Graph 公共客户端、Power Automate（网络不通）、群聊 @。

