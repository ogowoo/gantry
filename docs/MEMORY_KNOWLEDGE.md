# Memory / Knowledge / Skills（Batch 5）

## 三类信息边界

| 类型 | 内容 | 存储 | 注入方式 |
|---|---|---|---|
| Memory | 跨任务稳定事实、偏好、决定、踩坑 | `data/memory.db` | 任务文本检索 Top-5 |
| Knowledge | 项目文档、研究资料、历史问答 | `data/kb/` + 索引 | `kb` 工具/语义混合检索 |
| Skills | 经过验证的可复用操作流程 | `skills/*/SKILL.md` | 清单 + 按需 `skill_read` |

Memory 不写一次性任务流水账；Knowledge 不存 Agent 临时判断；Skills 只沉淀可复用流程。

## Memory 改进

`MemoryStore` 新增：

- `verified`：是否经过实际验证；
- `source`：证据来源（URL/文件/命令）；
- `expires_at`：可选过期时间；
- `confidence`：0~1 置信度。

`memory_write` 新增参数：

```text
verified=false
source=""
ttl_days=0
confidence=0.5
```

`memory_search` 会：

- 过滤已过期条目；
- 对 verified / 新近度 / confidence 加权；
- 返回 verified/source/updated/expires_at；
- 避免把过期或无法溯源的条目注入任务。

Agent 提示词会显示：

```text
[kind#id|verified] ...
[kind#id|unverified] ...
```

未验证不会被冒充为已验证。

## Knowledge 改进

- front-matter 支持 `expires` / `valid_until`，过期文档在检索时过滤；
- 结果保留 `source`、`updated`、`meta`；
- 语义混合检索结果也带 `source/updated`；
- 既有关键词 + 向量 + rerank 不变。

## Skills 改进

`skill_create` 新增 `verified`：

```text
verified=true 仅用于实际验证过的流程
```

SKILL.md front-matter 写入 `verified: true/false`，`skill_list` 返回该状态，
Agent 提示词显示 `[项目|已验证]` / `[全局|未验证]`。

未验证草稿仍可保存，但不会被伪装成已验证；后续任务可复核后覆盖。

## 检索边界

- Memory 只注入 Top-5 相关条目，不全文注入；
- Knowledge 结果带来源和时间，过期过滤；
- Skills 只注入描述清单，正文按需读取；
- 相关历史不应无条件塞进当前提示词。

## 测试

```powershell
.\.venv\Scripts\python.exe -W ignore -m unittest `
  tests.test_memory tests.test_memory_freshness `
  tests.test_kb tests.test_kb_freshness tests.test_kb_semantic `
  tests.test_skill_create tests.test_skill_verified
```

## 已知限制

- Memory 没有向量嵌入，仍是关键词 + 新近度/验证状态排序；
- Knowledge 的 `expires` 支持 ISO 日期/时间戳，其它自然语言失效日期不解析；
- Skills 的 verified 是元数据门，不是密码学证明；
- 记忆来源/置信度仍由模型填写，需要人工复核高风险事实。
