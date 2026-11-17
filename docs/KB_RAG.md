# 知识库：通用父子块与可插拔精排

日期：2026-11-17

Gantry 的知识库现在支持两件通用 RAG 能力，**不绑定法律领域**：

1. 父子分块 + 通用元数据；
2. 可插拔 reranker（默认关闭，零新增 Python 依赖）。

## 1. 父子块

```text
Markdown 标题小节
  └─ parent（完整小节，最多 1600 字）
       ├─ child 420 字左右
       ├─ child 420 字左右
       └─ ...
```

- **child**：参与 FTS / 向量召回。
- **parent**：不参与召回，命中 child 后按 `parent_id` 回查，交给最终生成。
- 老库第一次 `reindex` 会自动补建 parent，不需要手工删库。
- `stats()` 增加 `parents`；`chunks` 仍表示可召回块数量。

### 元数据

front-matter 保留键之外的任意键都会写入 `meta_json`：

```markdown
---
title: 合同违约责任
law: 中华人民共和国民法典
article: 第五百八十四条
source: 全国人大
category: 法律
page: 12
---
```

检索结果会带：

```json
{
  "parent_id": "...",
  "parent_text": "完整小节",
  "parent_heading": "合同编 › 违约责任",
  "meta": {"front": {"law": "...", "article": "..."}},
  "chunk_kind": "child"
}
```

`context_for()` 优先使用 parent 正文，并在来源行带 `law/article/source` 等元数据。

领域 profile 只需约定自己的字段（法律：`law/article`；合同：`contract/party`；医疗：`guideline/evidence`），不需要改通用检索代码。

## 2. 可插拔 reranker

默认关闭：

```json
"kb": {
  "rerank": {
    "enabled": false,
    "backend": "http",
    "base_url": "http://127.0.0.1:18087/v1",
    "model": "BAAI/bge-reranker-v2-m3",
    "api_key": "",
    "top_k": 3,
    "min_score": 0.0,
    "timeout": 30
  }
}
```

开启后流程：

```text
召回 Top-20（FTS / hybrid / vector）
  → POST {base_url}/rerank
  → 取 top_k（默认 3）
  → 失败则保留原排序，不阻塞检索
```

请求：

```json
{"model": "BAAI/bge-reranker-v2-m3",
 "query": "...",
 "documents": ["parent 或 child 文本", "..."],
 "top_n": 3}
```

兼容响应：

```json
{"results": [{"index": 0, "relevance_score": 0.93}]}
{"data":    [{"index": 0, "score": 0.93}]}
```

- 精排用 **parent 文本**（有 parent 时），不是 child 窗口；
- `min_score > 0` 时低于阈值的命中会被丢弃；
- reranker 抛错只记日志，检索继续返回原结果；
- `backend` 会显示 `fts5-trigram+rerank(...)` / `hybrid(...)+rerank(...)`。

## 3. 依赖边界

| 能力 | 新增依赖 |
|---|---|
| 父子块 + 元数据 | 0（纯 Python） |
| reranker 客户端 | 0（标准库 urllib） |
| reranker 模型 | 外部服务；Gantry 不装 PyTorch |
| BGE-M3 / sqlite-vec | 后续阶段，不在本次范围 |

所以当前可以先验证“切块 + 元数据 + 精排 + 引用”这条链路，再决定是否引入 BGE-M3 / ANN。

## 4. 相关代码

- `gantry/kb.py`：父子块、FTS、parent expansion、context
- `gantry/kb_semantic.py`：child 向量召回
- `gantry/kb_rerank.py`：reranker 协议与 HTTP 客户端
- `plugins/kb/plugin.py`：检索编排与精排开关
