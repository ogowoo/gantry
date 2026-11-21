# 长任务可靠性（Batch 4）

## 任务恢复

`task_recover(plan_id?)` / `task_run` 启动时自动恢复：

- 进程中断后遗留的 `running` 任务；
- `safe_retry=true`（默认）：回到 `pending/ready`，可继续执行；
- `safe_retry=false`：标记 `failed`，原因写明“不可安全重试，需人工确认”，绝不盲目重放外部副作用；
- 下游按依赖自动 `blocked`。

`task_run` 会先执行恢复再调度，所以重启后重复 `task_run` 不会重复启动已完成的子任务。

## 上下文保留

`task_summary` 写入 sessions 表，并进入：

- 压缩摘要 `summary.task_plan`；
- 续跑提示 `task_plan`；
- work ledger 的“任务图”；
- 任务工具的 todo 投影。

摘要包含 plan_id、状态、goal、进度、ready/blocked/failed 任务、验收条件和 outputs，
不复制完整历史/大结果。

## 错误分类

`gantry/error_taxonomy.py`：

| 类别 | 策略 |
|---|---|
| network / timeout / rate_limit | 有限重试 |
| permission | 停止，人工处理 |
| argument | 修参数/换工具，不原样重试 |
| business | 改变方案 |
| internal | 有限重试后停止 |

AgentRunner 的工具错误步骤现在记录：

```json
{"error_kind":"network","retryable":true,"recovery":"retry"}
```

编排器对子 Agent 的 `permission/argument` 错误不再盲目返工。

## 测试

```powershell
.\.venv\Scripts\python.exe -W ignore -m unittest `
  tests.test_task_plan tests.test_task_tools tests.test_task_context `
  tests.test_error_taxonomy tests.test_orchestrator
```

新增覆盖：安全/不安全恢复、任务摘要持久化、Agent 摘要/work ledger、错误分类、
非可重试权限错误不重试。

## 已知限制

- `safe_retry` 需要任务计划显式声明；默认 true 适合只读/可重做任务，外部付款/发送等应设 false；
- 自动恢复在 `task_run` 调用时触发，不是服务启动时扫描所有会话；
- 工具错误分类目前用于记录和编排决策，主 Agent 的工具错误不会自动重试（仍由模型改方案）；
- 压缩一致性校验目前是“摘要包含任务图”，尚未做 TaskStore 与摘要的自动 diff。
