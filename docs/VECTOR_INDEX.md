# Gantry 向量索引插件（BGE-M3 / sqlite-vec）

日期：2026-11-17

## 定位

`plugins/vector-index` 是**可选重型插件**，默认关闭。

它不修改 `data/kb.db` 的业务表，而是读取 KB 的 child chunks，建立独立索引：

```text
data/vector-index/vector-index.db
```

## 依赖

| 组件 | 必需 | 说明 |
|---|---|---|
| Gantry 核心 | 是 | 已有 |
| BGE-M3 embedding HTTP | 是 | OpenAI 兼容 `/embeddings`，默认 `http://127.0.0.1:18087/v1` |
| `sqlite-vec` | 否 | 有则原生 KNN；没有则 `numpy/纯 Python` 余弦 |
| `numpy` | 否 | 来自现有 fastembed 可选依赖；没有也能跑纯 Python |

Gantry 侧**不安装 PyTorch**；BGE-M3 由外部服务提供。

## 启用

插件默认 `default_enabled=false`。在插件管理里启用，或 `data/plugins.json`：

```json
{"vector-index": {"enabled": true}}
```

启用后：

- 工具：`vector_index_status` / `vector_index_reindex` / `vector_index_search` / `vector_index_reset`
- 面板：设置 → 插件面板 →「BGE-M3 向量索引」
- API：`api/status`、`api/admin`、`api/config`、`api/reindex`、`api/reset`、`api/search`、`api/health`

## 管理面板

设置 → 插件面板 → BGE-M3 向量索引：

- 查看模型/维度/后端/已索引块数/KB 块数
- 编辑 `base_url / model / dim / batch_size / top_k / index_backend / api_key`
- **保存配置**
- **重建索引**
- **测试连接**
- **清空索引**
- **检索测试**

面板是声明式 `admin`，插件不注入 HTML/JS；字段和动作由后端 schema 提供，前端渲染。

## 后端选择

```json
"index_backend": "auto"
```

- `auto`：`sqlite-vec` 可用就用，否则 `numpy`
- `numpy`：SQLite 存向量，查询时余弦排序（适合中小库/没有扩展）
- `sqlite-vec`：`pip install sqlite-vec`，原生 KNN；失败会报错，不静默假装成功

## BGE-M3 服务示例

Infinity / llama.cpp / 自建 OpenAI 兼容 embedding 服务都可以，只要：

```http
POST {base_url}/embeddings
{"model": "BAAI/bge-m3", "input": ["..."]}
```

返回：

```json
{"data": [{"embedding": [0.1, 0.2, "..."]}]}
```

默认维度 1024；如果服务返回 512/Qwen3 等其它维度，把面板里的 `dim` 改成实际维度。

## 限制

- 本插件不负责 KB 入库；先由 KB 插件 `kb_reindex` 生成 child/parent。
- `sqlite-vec` 是原生扩展，不属于纯 Python；没有它时是暴力余弦，不是 ANN。
- 索引和 embedding 服务密钥保存在 `data/plugins.json`；该文件不入库。
- 向量索引独立于 `data/kb.db`，删除/重置不会删除原文。
