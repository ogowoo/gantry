# kb（本地知识库）

`data/kb/**/*.md` → SQLite FTS5（trigram）索引，给模型提供 `kb_search` / `kb_reindex` / `kb_stats`。
与 `web-search` 对称：一个查本地，一个查互联网。

## 用法

```powershell
# 放文档（支持子目录；文件头可写 front-matter）
#   ---
#   title: VPN 连不上怎么办
#   tags: vpn, 网络
#   ---

curl "http://127.0.0.1:8765/v1/plugins/kb/api/search?q=VPN%20连不上&n=3"
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/reindex -d '{\"force\":false}'
curl http://127.0.0.1:8765/v1/plugins/kb/api/stats
curl http://127.0.0.1:8765/v1/plugins/kb/api/docs
```

## 检索方式

- **切块**：按 `#`/`##` 标题切，文档名与标题路径一起入索引；
- **分词**：FTS5 `trigram`（中文按 3 字符切，不需要分词器）；
- **打分**：`0.75 × 查询词覆盖率 + 0.25 × bm25 名次`（满分 1.0；只沾一个词约 0.3）；
- **引用**：每条命中带 `cite`（`《标题》 › 小节`），适合直接写进回答里。

字面匹配的短板是同义改写会漏（"上不了外网"未必命中只写"隧道"的文档）。
要上向量检索，另写一个 `kb_semantic` 工具（embedding + sqlite-vec），本插件不含。

## 配置（`data/plugins.json` 里 kb.config）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `dir` | `data/kb` | 文档目录（相对项目根，或绝对路径） |
| `max_results` | `6` | 默认返回条数 |
| `min_score` | `0.0` | 最低相关度（0~1） |
| `auto_reindex` | `true` | 索引为空时首次搜索自动建索引 |

索引库默认在 `data/<目录名>.db`（例如 `data/kb.db`）。

## 工具

| 工具 | 说明 |
| --- | --- |
| `kb_search(query, limit?, min_score?)` | 查库，返回 `{title, heading, path, text, score, cite}` |
| `kb_reindex(force?)` | 增量重建（`force=true` 全量） |
| `kb_stats()` | 文档数 / 分块数 / 字符数 / 是否 FTS |
