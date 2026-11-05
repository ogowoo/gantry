# Teams 网页版 IT 应答机器人（teams-bot）

在 **Teams 网页版**里，只要 1:1 聊天或群里有人 **@ 我**，就用本地知识库 + Gantry 的模型
自动回复。**零 Graph/Bot Framework 授权**：驱动的是已登录的 Teams 网页（同一套浏览器
上下文，和 Copilot / OWA 自动化共用 SSO）。

```
有人发 "@Gantry 打印机脱机了"
        │
        ▼  轮询（默认 20s，只看未读/指定会话）
gantry/teams.py        读消息 / 发消息（Playwright，选择器带多级兜底）
        │
        ▼
gantry/teams_logic.py  要不要回？问什么？（@识别、去重、剥掉 @）
        │
        ├─ 知识库命中（覆盖率≥阈值）→ 直接拼答案 + 出处（不调模型，快且不会编）
        ├─ 未命中 + fallback_agent → Gantry 模型（提示词强制"只依据资料"）
        └─ 都没有 → "知识库里没有，建议找 IT 服务台"
        │
        ▼
回帖（默认 dry_run，只记录"本来会回什么"）
```

## 组成

| 文件 | 作用 |
| --- | --- |
| `gantry/teams.py` | 网页自动化：`teams_open` / `list_chats` / `open_chat` / `recent_messages` / `send_message` / `probe` |
| `gantry/teams_logic.py` | 纯逻辑：@ 识别、问题抽取、去重键、KB 答案拼装、`should_answer` |
| `gantry/kb.py` | 知识库：`data/kb/**/*.md` → SQLite FTS5（trigram）+ 覆盖率打分 |
| `plugins/teams-bot/` | 插件：轮询循环、工具/接口/面板、dry_run 保护 |
| `scripts/kb.py` | 命令行：`index` / `search` / `stats` / `sample` |

## 知识库

放进 `data/kb/`（可建子目录），Markdown 就行：

```markdown
---
title: VPN 连不上怎么办
tags: vpn, 网络, 远程办公
---

# VPN 连不上怎么办

## 排查步骤
1. …（回答里会带「《VPN 连不上怎么办》 › 排查步骤」这样的出处）
```

```powershell
python scripts/kb.py sample            # 首次放一篇格式样例
python scripts/kb.py index             # 建/更新索引（增量，按 mtime）
python scripts/kb.py search "VPN 连不上"   # 看命中和分数，用来调 kb_min_score
python scripts/kb.py stats
```

- 索引在 `data/kb.db`；插件每 60 秒会增量重建一次，扔进新文件不用重启。
- 中文能搜是因为 FTS5 用了 **trigram** 分词（默认 unicode61 不切中文，整句查不到）。
- 打分 = 0.75 × 查询词覆盖率 + 0.25 × bm25 名次：全覆盖 ≈1.0，只沾一个词 ≈0.3，
  `kb_min_score`（默认 0.35）才真正能筛掉弱命中。

## 启用（默认是"装好但不动作"）

```jsonc
// data/plugins.json → teams-bot
{"enabled": true, "dry_run": true, "chats": ["IT 支持群"], "mention_tokens": ["@Gantry"],
 "my_name": "你的显示名", "model": "copilot-auto"}
```

也可以走接口改（不用重启）：

```powershell
curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/settings `
  -H 'Content-Type: application/json' `
  -d '{"enabled":true,"dry_run":true,"chats":["IT 支持群"]}'
curl http://127.0.0.1:8765/v1/plugins/teams-bot/api/status     # 看本轮"本来会回什么"
curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/run  # 手动跑一轮
```

`dry_run` 下 `would[]` 里能看到**它准备回什么**（含来源 `kb:标题` / `model` / `unknown`），
确认没问题再同时打开两个闸：`{"dry_run": false, "send_enabled": true}`。

## 校准（Teams 改版时必做）

选择器写在 `gantry/teams.py` 的 `_SEL` 里，每个位置都有多条候选。Teams 改版后先跑：

```powershell
curl http://127.0.0.1:8765/v1/plugins/teams-bot/api/probe
```

它会返回：命中的选择器及数量、会话列表、最近消息、输入框是否找到，
并把页面 `data/teams-probe/teams-<时间>.html/.txt` 落盘。**先看这个再改选择器，别猜。**

## 已知边界

- **必须有人先在自动化浏览器里登录 Teams**（和 Copilot 同一个 profile）；没登录时
  `teams_open` 会报"Teams 要求登录"，日志里有记录。
- 纯 HTTP 访问 `teams.cloud.microsoft` 会被判 `unsupported-browser`——必须走 Playwright
  的真 Chromium/Edge，这一点 `teams_open` 里有明确报错。
- 消息身份用 `(作者, 文本, 页内序号)` 哈希去重：同作者同文本且位置互换的极端情况可能重复回。
- 只做**文本**：Teams 卡片/附件不解析；单条回复有长度上限（知识库答案默认截到 1200 字）。
- 回复是"当前登录账号"发的（用户选择），所以显示为你本人——想换专用账号，就在另一个
  Chrome profile 里登录 Teams，然后把 `browser_profile_dir` 指过去。


## 路线 B：直接读 Teams 的 IndexedDB（当前主路径）

点会话列表一直不可靠（点中心会命中头像弹资料卡、JS `click()` 被 React 忽略、坐标点击不切换），
但 Teams 网页会把**会话与最后一条消息缓存在 IndexedDB**，直接读它更稳：

| 库 | 仓库 | 用途 |
| --- | --- | --- |
| `Teams:conversation-manager` | `conversations` | 会话 id / `threadProperties` / **`lastMessage`** / `lastMessageTimeUtc` |
| `Teams:profiles` | `profiles` | mri → 显示名（用于把私聊标注成「Lynn Ling - DSV」） |

要点（都是实测踩出来的）：

- **私聊判定看 id 形态**：`19:<guid>_<guid>@unq.gbl.spaces` 是私聊，`19:<opaque>@thread.v2`
  / `@thread.tacv2` 是群聊。**不能**用 `memberProperties` 的键数——它是单个成员对象
  （键固定为 `relationshipState`/`role`），按它判断会把 351 条里的 289 条都误判成私聊。
- 私聊 id 里的两个 GUID 有一个是 **Skype 租户**（从 IndexedDB 库名 `skypexspaces-<guid>` 取），
  另一个才是对方 → 用 `profiles` 换成名字。
- `messages` 字段是**URL 字符串**（chatsvc 端点），拿不到历史；但 `lastMessage` 对象里有
  正文 / 发送者 / `clientmessageid` / `composetime`，**轮询最后一条**就够做应答机器人。
- 新消息检测：比较每个会话的 `lastMessageTimeUtc` + `lastId`，变化且含 @、且不是自己发的才处理。

### 写侧：按"最后一条消息"搜索打开会话（已打通）

点会话项、深链、按人名搜索都不行（人名会开**联系人名片**，不是聊天）。最终可行的是：

1. 从 IndexedDB 拿到目标会话的 **`lastText`**（最后一条消息原文）；
2. 在搜索框里**逐字敲入**它的一段（必须用真实按键 `keyboard.type`，`fill()` 不触发建议）；
3. 建议列表里点 **"Recent messages"** 那一行 → 直接打开该聊天；
4. 输入框 `[data-tid="ckeditor"]` 输入 → 点发送按钮 `[data-tid="sendMessageCommands-send"]`。

实测（2026-11-02）：

```
{"opened": true, "how": "snippet", "header": "Lynn Ling - DSV …",
 "sendButton": true, "cleared": true, "sent": false}
```

发送有两道闸，默认都关：

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| `dry_run` | `true` | 只把"本来会回什么"写进 `status.last_cycle.would[]` |
| `send_enabled` | `false` | 写侧总闸；即使 `dry_run=false`，不开它也不会点发送 |

两个都打开（`{"dry_run": false, "send_enabled": true}`）才会真的回消息。演练（不发送）用：

```powershell
curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/compose `
  -H 'Content-Type: application/json' `
  -d '{"peer":"Lynn Ling","snippet":"我在BPM下载下来的","text":"测试内容","send":false}'
# send=false 时只输入并清空输入框，不会发出去
```

## 最短路径：让它用知识库（含向量）回答

1. **放资料**：`data/kb/**/*.md`（文件头可写 `title/tags/category`）。
2. **建索引**（一次，进程内 embedding，无需外部服务）：

   ```powershell
   python scripts/kb.py index      # FTS5
   python scripts/kb.py embed      # 向量（config.json 的 kb.embedding，backend=local）
   ```

3. **打开机器人**（只算不发，先看效果）：

   ```powershell
   curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/settings ^
     -H "Content-Type: application/json" ^
     -d "{\"enabled\":true,\"dry_run\":true,\"send_enabled\":false,\"kb_first\":true,\"fallback_agent\":false,\"my_name\":\"Walt Liang - DSV\"}"
   ```

   - `kb_first: true` → 命中知识库后让模型**依据资料组织回答**（带出处），避免整段抄文档；
     模型不可用时退回原文摘录。想恢复"完全不调模型"可设 `kb_synthesize: false`；
   - `fallback_agent: false` → 没命中就不硬编（配合 `reply_when_unknown` 决定要不要回"转人工"）。

4. **单条验证**（不发送、不用等有人 @）：

   ```powershell
   curl "http://127.0.0.1:8765/v1/plugins/teams-bot/api/answer?q=VPN%%20连不上怎么办"
   # → {"text":"…","source":"model+kb:《VPN 连不上怎么办》","would_reply":true}
   ```

5. **看整轮**：`POST api/run` 后看 `GET api/status` 的 `last_cycle.would[]`（dry_run 下不发送）。
6. **真要发**：`{"dry_run": false, "send_enabled": true}`（两个都要开）。

## 发送安全（P0）

- 后台轮询、手动 `api/run`、`teams_reply`、`api/compose` 共用 `gantry.teams` 的页面互斥锁；
  `cycle()` 非重入：上一轮未结束时 `api/run` 返回 `skipped=busy`，不会排队叠加。
- 每条自动回复发送前核对 `conversation_id`、会话标题、对端 `peer_guid`/显示名和触发消息片段；
  任一不一致返回 `reason=identity_mismatch`，**不发送**。仅当 chatsvc 列表/直查都找不到该
  1:1（API 根本发不了）时才回退 UI，回退仍校验标题/对端；明确不一致时绝不回退。
- 幂等箱 `data/teams-bot-sends.db` 以 `(conversation_id, message_id)` 为主键记录
  `reserved / sent / failed`；重启、超时、重复轮询都不会重发。`reserved` 超过租约或
  `failed` 视为"结果未知"，默认不自动重试，需人工确认。

检索走 `gantry.kb_semantic.hybrid`（0.75×余弦 + 0.25×词面覆盖率），与 kb 插件、CLI 完全一致；
向量没建好时自动退回 FTS，不会报错。阈值用 `kb_min_score`（默认 0.35；混合分一般 0.5+，
觉得太宽松可提到 0.45~0.5）。

## 登录 Teams（含 MFA）

机器人用的是**自动化浏览器里已登录的 Teams 会话**。如果 `api/status` 显示
`session.auth=expired`，或 `api/idb-watch` 返回 `{"ok":false,"auth":"expired"}`，
说明需要在这个浏览器里登录一次（MFA 慢没关系，登录态会一直保留在 `EdgeProfile/`）。

```powershell
python scripts/login_teams.py            # 把窗口带到前台，最多等 15 分钟
python scripts/login_teams.py --timeout 1800   # MFA 特别慢就放宽
```

它做的事：调 `POST /v1/plugins/teams-bot/api/open-teams` 把 Teams 标签**切到前台**，
然后每 10s 查一次 `api/idb-watch`，登录成功就退出。期间**不要关窗口、不用重启服务**。

也可以手动调接口：

```powershell
curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/open-teams ^
  -H "Content-Type: application/json" -d "{\"wait\": 8}"
# {"ok":true,"url":"https://teams.cloud.microsoft/v2/","title":"Microsoft Teams","logged_in":false,"hint":"..."}
```

常见现象：**"Teams 页面不弹出"** —— 因为机器人会复用 `current_page()`（可能停在
Copilot 的登录墙），并不会自己弹新窗口。用上面的命令会显式把标签导航到 Teams 并
`bring_to_front()`。登录成功后，下一轮 cycle 会自己把 `session.auth` 变回 `ok`。

