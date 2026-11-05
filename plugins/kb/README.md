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

## 向量检索（语义召回）

字面匹配会漏同义改写。加上向量层后，`kb_search` 默认 `mode=auto`：**有向量索引就混合检索**，
否则退回 FTS。混合分 = `w_vec × 余弦 + w_cov × 词面覆盖率`（默认 0.75 / 0.25）——
纯余弦对"分机 8000""RITM 编号"这类专有串不敏感，词面覆盖率把它拉回来。

实测（本机 12 分块，Qwen3-Embedding-0.6B 1024 维）：

| 查询 | 纯 FTS | 混合 |
| --- | --- | --- |
| `公司网络访问不了外部网站` | 1 条弱命中（0.138） | **0.603** 命中《Wi-Fi 802.1X 认证失败 › 症状》 |
| `企业微信登录不上` | 0 条 | 仍有可解释结果 |

配置（`plugins/kb/plugin.json` 的 `config.embedding`，或 `data/plugins.json`）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `backend` | `fastembed` | `fastembed`=进程内 ONNX（推荐）；`auto`=HTTP 端点可用就用，否则退回 FTS；`http` / `hash` / `local` / `off` |
| `model` | `BAAI/bge-small-zh-v1.5` | fastembed 模型名（中文 512 维）；`http` 时是端点模型名 |
| `cache_dir` | `data/models/fastembed` | fastembed 模型缓存目录 |
| `local_files_only` | `false` | 只读缓存、不联网下载（已预下载时设 `true`） |
| `base_url` | `http://127.0.0.1:18085/v1` | `http` 后端：任意 OpenAI 兼容 `/embeddings`（llama.cpp / Ollama / FLM） |
| `model_path` | 空 | `local` 后端：进程内 GGUF（llama_cpp）路径 |
| `dim` | `512` | 0=自动探测（fastembed 默认 512） |
| `w_vec` / `w_cov` | `0.75` / `0.25` | 混合权重 |

装 fastembed（约 50 MB 依赖，模型首次使用自动下载约 90 MB）：

```powershell
python install.py --embed
# 或：
pip install -r requirements-embed.txt
```

`fastembed` 是默认的**进程内**模式，不需要 llama.cpp/外部 server。模型缓存目录默认
`data/models/fastembed`；离线机器可以先在有网环境下载整个目录，再把 `local_files_only`
设为 `true`。

`local` 是另一种**进程内**模式：给 `model_path` 指向 embedding GGUF（如
`Qwen3-Embedding-0.6B-Q8_0.gguf`），由 `gantry.local_models.LocalEmbedder` 用 `llama_cpp`
直接加载，不需要外部 `llama-server`。

`hash` 是**零依赖兜底**（字符 n-gram hashing）：不联网、不下载模型，语义能力弱但确定性强，
适合离线环境与单测。

建索引与查询：

```powershell
python scripts/kb.py embed                      # 增量建向量（只算新增分块）
python scripts/kb.py search "上不了外网" --mode hybrid
python scripts/kb.py vector-stats

curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/embed -d "{\"force\":false}"
curl "http://127.0.0.1:8765/v1/plugins/kb/api/semantic?q=上不了外网&n=3"
curl http://127.0.0.1:8765/v1/plugins/kb/api/vector-stats
```

工具：`kb_embed(force)` 建向量；`kb_search(..., mode="fts|vector|hybrid|auto")`。
向量存在同一个 `data/kb.db` 的 `chunks_vec` 表（`chunk_id/model/dim/vec/at`），
换模型或维度会自动整体重建，避免混用不同向量空间。

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
| `kb_embed(force?)` | 建/更新**向量**索引（语义检索用） |
| `kb_stats()` | 文档数 / 分块数 / 字符数 / 是否 FTS |

## 导入文档（Word / PDF / Excel / PPT / HTML / URL / 粘贴）

管理页：`/ui/` → ⚙ 设置 → 📚 知识库（拖拽、选文件、URL、粘贴、收件箱、删除、检索测试）。
接口：

```powershell
# 文件（Base64）
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/import ^
  -H "Content-Type: application/json" -d "{\"name\":\"vpn.docx\",\"data_b64\":\"<base64>\"}"

# 网页
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/import ^
  -H "Content-Type: application/json" -d "{\"url\":\"https://example.com/faq\"}"

# 粘贴文本
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/import ^
  -H "Content-Type: application/json" -d "{\"text\":\"打印机脱机先重启 spooler\",\"title\":\"打印机\"}"

# 收件箱：把文件丢进 data/kb/inbox/，然后
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/inbox/scan

# 删除
curl -X POST http://127.0.0.1:8765/v1/plugins/kb/api/delete ^
  -H "Content-Type: application/json" -d "{\"path\":\"imported/printer.md\"}"
```

提取全部用 Python 库：`python-docx` / `pypdf` / `openpyxl` / `python-pptx` / `beautifulsoup4`；
**扫描版 PDF 与图片**走 RapidOCR（ONNX、CPU、PP-OCRv6 模型，`pip install rapidocr`，首次运行自动下模型）；
`.doc/.xls/.ppt` 老格式会提示先另存为新格式；扫描版 PDF 没有文本层会明确报错（需先 OCR）。
落盘位置 `data/kb/imported/<slug>.md`，front-matter 带 `title/category/source/updated`，
同名按来源覆盖（重复导入不会产生副本）；每次导入都会重建 FTS 与向量索引。

