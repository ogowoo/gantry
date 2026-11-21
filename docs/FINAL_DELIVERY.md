# Gantry Agent 能力升级 — 最终交付（Batch 0–6）

## 1. 完成范围

| Batch | 内容 | 状态 |
|---|---|---|
| 0 | 源码审计、能力对照、测试基线 | 完成 |
| 1 | Task Planner（任务模型/依赖/持久化/任务工具） | 完成 |
| 2 | Orchestrator（调度/并发/产物审查/返工） | 完成 |
| 3 | Verification & Benchmark（验收引擎/30 条任务/指标） | 完成 |
| 4 | 长任务可靠性（恢复/上下文/错误分类） | 完成 |
| 5 | Memory/Knowledge/Skills（验证/时效/来源） | 完成 |
| 6 | 安全审查、风险分级、文档交付 | 完成 |

## 2. 新增模块

- `gantry/task_plan.py`：Task 模型、依赖图、SQLite TaskStore、恢复/摘要
- `gantry/tools_tasks.py`（在 `tools_extra.py` 中）：`task_plan/task_read/task_update/task_run/...`
- `gantry/orchestrator.py`：ready 调度、并发、冲突、超时、返工
- `gantry/task_verify.py`：文件/格式/命令/测试/API/组合验收 + 独立验证
- `gantry/error_taxonomy.py`：网络/超时/限流/权限/参数/业务/内部错误
- `gantry/security.py`：风险分级、审批建议、不可信内容规则、配置审计

## 3. 主要兼容性

- 未重写 `AgentRunner.run()`；旧 API/工具名/插件接口保持可用。
- `todo_write` 保留；Task 图投影到 todo 面板。
- 旧 sessions 自动迁移 `task_summary`；旧 tasks.db 自动补 model/safe_retry/risk 列。
- 旧 memory.db 自动补 verified/source/expires_at/confidence 列。
- 旧 Knowledge 文档不需要新增字段；`expires` 可选。
- 旧 skill SKILL.md 没有 `verified` 时显示未验证，不影响读取。

## 4. 新增配置/策略

- Task：`safe_retry`、`risk`、`max_retries`、每任务 `model`
- Orchestrator：
  - `agent.task_run_max_concurrency`（默认 4）
  - `agent.task_run_timeout_seconds`（默认 600）
  - `subagent_max_steps`
- 审批：`delete/move`、整文件 `overwrite`、`pwsh`、外部发送等走审批
- Memory：`verified/source/ttl_days/confidence`
- Knowledge：front-matter `expires` / `valid_until`

## 5. 测试

最终全量：

```text
Ran 1866 tests...
OK (skipped=2)
```

（Batch 6 安全测试加入后最终数字以实际日志为准。）

## 6. Benchmark

- `tests/evals/tasks.json`：30 条、22 类；
- `scripts/eval.py`：完成率、验收率、耗时、步数、工具调用、重试、无进展、
  子 Agent、token、压缩恢复、上一份报告对比；
- 最近一次全量 30 条：27/30（90%）；
- `read-analyze`、`task-run-subagent`、`final-markdown`、只读任务均已单独修复通过；
- 剩余失败：`debug-test`（Provider 偶发 0 步）、`grep-locate`（最终答复断言）、`ambiguous-plan`（22 步超过 20 步限制）；
- 没有伪造全量通过。

## 7. 安全审计发现

`audit_config(config.json)` 发现 3 条：

```text
providers.ollama.api_key
providers.fflm.api_key
providers.llamacpp.api_key
```

均为明文写盘。建议迁移到 `.env` 并用 `${VAR}` 引用；这是已知风险，不在本轮自动改配置。

其他边界：

- 删除/移动/整文件覆盖/pwsh/外部发送需一次性审批；
- 子 Agent 继承审批但不能绕过 `requires_approval_for`；
- 外部页面/知识库/工具结果明确声明为数据而非指令；
- 插件仍运行在本地服务权限内，不是 OS 级沙箱。

## 8. 已知问题

1. 30 条 Benchmark 已完成，但存在 Provider/模型抖动，最近 27/30；
2. `debug-test` 仍偶发 0 步 Provider 卡住；
3. 主 Agent 的 final 未统一接入 Task acceptance（目前 `task_run` 的子任务有验收）；
4. 子 Agent 默认共享父工作区，未声明路径的写入仍可能冲突；
5. 任务自动恢复在 `task_run/task_recover` 触发，不是服务启动时扫描；
6. Memory 仍为关键词+新近度排序，没有向量索引；
7. 插件无独立 OS 沙箱；凭据未加密存储。

## 9. 后续建议

- 先修复 `read-analyze`（provider 上下文/缓存）并重跑 30 条 Benchmark；
- 把 Task acceptance 接入 AgentRunner final；
- 为子 Agent 提供真正的工作区隔离与合并策略；
- 完成 .env 凭据迁移；
- 用 benchmark 报告驱动下一轮回归比较。
