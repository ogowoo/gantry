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
确认没问题再：`{"dry_run": false}`。

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
| `Teams:profiles` | `profiles` | mri → 显示名（用于把私聊标注成「Lynn Ling - Example」） |

要点（都是实测踩出来的）：

- **私聊判定看 id 形态**：`19:<guid>_<guid>@unq.gbl.spaces` 是私聊，`19:<opaque>@thread.v2`
  / `@thread.tacv2` 是群聊。**不能**用 `memberProperties` 的键数——它是单个成员对象
  （键固定为 `relationshipState`/`role`），按它判断会把 351 条里的 289 条都误判成私聊。
- 私聊 id 里的两个 GUID 有一个是 **Skype 租户**（从 IndexedDB 库名 `skypexspaces-<guid>` 取），
  另一个才是对方 → 用 `profiles` 换成名字。
- `messages` 字段是**URL 字符串**（chatsvc 端点），拿不到历史；但 `lastMessage` 对象里有
  正文 / 发送者 / `clientmessageid` / `composetime`，**轮询最后一条**就够做应答机器人。
- 新消息检测：比较每个会话的 `lastMessageTimeUtc` + `lastId`，变化且含 @、且不是自己发的才处理。

### 写侧现状（未打通）

**发送**需要先切到目标会话，而切会话的三条路都试过且失败：
点会话项（点到头像/资料卡）、搜索框（`AUTOSUGGEST_INPUT` 填了不弹建议）、深链
（`/v2/#/chat/<id>` 等四种形式都不切换）。

因此插件默认 `send_enabled: false`：只把"本来会回什么"写进 `status.last_cycle.would[]`。
等写侧打通（或改成"只回当前打开的会话"）后再打开。
