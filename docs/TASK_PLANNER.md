# Task Planner（Batch 1）

复杂任务的持久任务图。它不改变 Agent 执行循环：模型通过 `task_*` 工具维护任务，
AgentRunner 继续负责“取动作 → 执行工具 → 记录步骤”。

## 数据模型

每个计划属于一个会话，最多 50 个任务。

```json
{
  "id": "scan",
  "title": "扫描项目",
  "description": "列出安全相关文件",
  "status": "pending|ready|running|blocked|completed|failed|cancelled",
  "priority": 0,
  "executor": "main|subagent",
  "assignee": "",
  "model": "optional per-task subagent model",
  "depends_on": ["another-task-id"],
  "input": {},
  "outputs": [{"path": "report.md"}],
  "acceptance": ["report.md 存在"],
  "failure_reason": "",
  "retry_count": 0,
  "max_retries": 1,
  "safe_retry": true
}
```

- 根任务自动 `ready`；
- 有未完成依赖的任务 `pending`；
- 前置失败/取消时，下游自动 `blocked`；
- 完成/失败/取消会写入任务 `history`；
- `safe_retry=false` 的任务在进程中断后不会自动重放，标记 failed 等人工确认；
- `task_read` 返回 `ready` 列表和进度统计。

## 工具

| 工具 | 作用 |
|---|---|
| `task_plan(goal, tasks, title?, replace?)` | 创建持久任务图；一个会话同时只有一个活动计划 |
| `task_read(plan_id?)` | 读取计划、ready 任务、进度、输出/失败原因 |
| `task_update(task_id, status?, outputs?, acceptance?, ...)` | 更新状态/输出/验收/依赖；失败会阻塞下游 |
| `task_cancel(task_id?, plan_id?)` | 取消单个任务或整个计划 |
| `task_resume(task_id?, plan_id?)` | 恢复失败/取消/阻塞任务或取消的计划 |
| `task_replan(updates, plan_id?)` | 修改依赖/标题/验收/新增任务，只重算受影响下游 |

旧的 `todo_write` / `todo_read` / 子代理工具保持兼容。任务图会投影成 todo 面板的
`pending / in_progress / completed` 三态，完整状态用 `task_read` 查看。

## 存储

默认 `data/tasks.db`，独立于 `sessions` 表，避免给核心会话库做迁移：

- `task_plans`：`plan_id / session_id / goal / title / status / revision`
- `task_items`：任务全量字段 + 依赖/输出/验收/历史 JSON

`task_db_path` 可在 `agent` 配置或工具注册时覆盖（测试用临时数据库）。

## 提示词

`task_plan` 工具可用时，系统提示词注入“任务图（复杂任务）”段：

- 3 个以上子目标、跨文件或有依赖时先建图；
- 只开始 `ready` 任务；
- 完成/失败立即 `task_update`；
- 需求变更用 `task_replan`，不重做已完成任务；
- 简单任务不建图。

## 测试

```powershell
.\.venv\Scripts\python.exe -W ignore -m unittest tests.test_task_plan tests.test_task_tools
```

已覆盖：依赖/循环、ready 顺序、失败下游 blocked、持久化、取消/恢复、replan、
工具错误和 todo 投影。

## 后续 Batch

- Batch 2：Orchestrator 按 task graph 调度子 Agent，审查实际产物；
- Batch 3：`acceptance` 的通用验证器与 30 条 Benchmark；
- Batch 4：任务图压缩/恢复与工具错误分类。
