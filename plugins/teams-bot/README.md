# teams-bot

Teams 网页版里"有人 @ 我"就回，**知识库优先**。默认装好但不动手（`enabled=false`、
`dry_run=true`）。

## 快速开始

1. 写入知识库：把 IT 问答 `.md` 丢进 `data/kb/`（`python scripts/kb.py sample` 会给一篇样例）；
   `python scripts/kb.py index` 建索引，`python scripts/kb.py search "问题"` 验证命中。
2. 确认 Teams 已登录：自动化浏览器里打开一次 `https://teams.cloud.microsoft/v2` 并完成 SSO。
3. 打开开关（先保持 dry_run）：

   ```powershell
   curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/settings -H "Content-Type: application/json" -d '{\"enabled\":true,\"dry_run\":true,\"chats\":[\"IT 支持群\"],\"mention_tokens\":[\"@Gantry\"],\"my_name\":\"你的显示名\"}'
   curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/run
   curl http://127.0.0.1:8765/v1/plugins/teams-bot/api/status
   ```

   `status.last_cycle.would[]` 是"本来会回什么"（含来源 `kb:标题` / `model` / `unknown`）。
4. 确认无误后再打开写侧总闸：

   ```powershell
   curl -X POST http://127.0.0.1:8765/v1/plugins/teams-bot/api/settings -H "Content-Type: application/json" -d '{\"dry_run\":false,\"send_enabled\":true}'
   ```

   `dry_run=false` 且 `send_enabled=true` 两个都满足才会真的点发送。

## 工具（agent 也能用）

| 工具 | 说明 |
| --- | --- |
| `teams_status` | 机器人状态、知识库规模、最近一轮结果 |
| `teams_reply(chat, text, force)` | 手动发一条（dry_run 下需 `force=true`） |
| `teams_probe` | 校准用：dump 命中的选择器 / 会话 / 消息，落盘 `data/teams-probe/` |
| `kb_search(query, limit)` | 查知识库（带出处与分数） |
| `kb_reindex()` | 增量重建索引 |

## 配置项

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关 |
| `dry_run` | `true` | 只记录不发送 |
| `send_enabled` | `false` | 写侧总闸；`dry_run=false` 且它为 `true` 才真发 |
| `source` | `idb` | 读会话方式：`idb`（推荐）/ `dom` |
| `interval_seconds` | `20` | 轮询间隔 |
| `chats` | `[]` | 只盯这些会话（标题片段）；空=扫描**未读**会话 |
| `mention_tokens` | `["@Gantry"]` | 什么算"@我" |
| `my_name` | `""` | 自己的显示名，用来排除自己发的 |
| `kb_min_score` | `0.35` | 知识库命中阈值（`scripts/kb.py search` 可看分数） |
| `kb_max_chars` | `1200` | 知识库答案截断长度 |
| `fallback_agent` | `true` | 未命中时用 Gantry 模型答 |
| `agent_mode` | `false` | 模型走 Agent（能调工具，慢）还是纯对话 |
| `model` | `copilot-auto` | 可用任意 `/v1/models` 里的模型（含 provider 模型） |
| `max_replies_per_cycle` | `2` | 每轮最多回几条，防刷屏 |

详见 [docs/TEAMS-BOT.md](../../docs/TEAMS-BOT.md)。
