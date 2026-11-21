# Task Orchestrator（Batch 2）

按任务图调度 `executor=subagent` 的 `ready` 任务；复用现有 `subagents.run_subagent`，
不创建第二套子 Agent 运行时。

## 行为

- 只执行 `status=ready` 且 `executor=subagent` 的任务；
- 每个任务可以使用自己的 `model`；未设置时使用 `task_run(model=...)`；
- 依赖未完成的 `pending` 任务不会启动；
- 同一批任务按优先级和并发上限执行（默认 `agent.task_run_max_concurrency` 或 4）；
- 两个并行任务声明同一个 output 路径时不会同时执行；已完成的路径被另一个任务再次声明时，
  第二个任务直接失败，避免静默覆盖；
- 单任务超时（默认 600 秒）记为失败；
- 失败按 `max_retries` 返工；返工原因写进任务历史；
- `executor=main` 的任务不交给子 Agent，返回 `needs_main`，由主 Agent 自己完成。

## 结果审查

`gantry/task_verify.py` 是 Batch 2 的最小产物审查器：

- `outputs` 里的文件/目录真实存在且非空；
- `acceptance` 支持 `file_exists`、`file_contains`、`file_min_bytes`、
  `final_contains`、`tests_pass`、`syntax_pass`；
- 子 Agent 的 `verification` 来自它时间线里真实的 `validate_python` /
  `run_unittest` 成功步骤，不是它嘴上说通过；
- 没有机器可检查条件时明确 `verified=False`，不把文本声称当成已验证。

Batch 3 会扩展命令、API、格式和组合条件。

## 工具

```text
task_run(plan_id?, model?, max_concurrency?, timeout_seconds?, max_steps?)
```

`task_run` 返回：

```text
completed / failed / retrying / verified / needs_main
progress
results[]（task_id、attempts、review、agent_id、content、error）
```

`needs_main` 非空时，主 Agent 继续执行这些任务，然后再次 `task_update` / `task_run`。

## 配置

```json
{
  "agent": {
    "task_run_max_concurrency": 4,
    "task_run_timeout_seconds": 600,
    "subagent_max_steps": 80
  }
}
```

## 测试

```powershell
.\.venv\Scripts\python.exe -W ignore -m unittest `
  tests.test_task_verify tests.test_orchestrator tests.test_task_tools
```

覆盖：依赖顺序、并发上限、失败下游阻塞、超时、返工、产物冲突、main 任务、
`task_run` 工具与产物审查。

## 已知限制

- 子 Agent 默认共享父工作区；声明的 output 路径会防冲突，但未声明路径的写入无法完全隔离；
- 超时使用 `asyncio.wait_for` 取消等待，底层子会话可能仍需后续清理；
- 通用命令/API/格式验收在 Batch 3。
