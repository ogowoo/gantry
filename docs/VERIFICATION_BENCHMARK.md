# Verification & Benchmark（Batch 3）

## 验收引擎

`gantry/task_verify.py` 负责确定性验收，不依赖模型总结：

| 条件 | 说明 |
|---|---|
| `file_exists` | 文件/目录存在 |
| `file_contains` | 文件包含文本（可 `regex`） |
| `file_min_bytes` | 最小字节数 |
| `file_sha256` | 精确哈希 |
| `format` | `json` / `pdf` / `png` / `zip` / `nonempty` |
| `tests` | 真实运行 `python -m unittest discover`（需 `allow_commands`） |
| `command` | 真实命令 + exit code + stdout 包含 |
| `api` | HTTP 状态 + JSON path / equals / contains（需 `allow_commands`） |
| `all_of` / `any_of` / `not` | 组合条件 |
| `final_contains` | 最终答复包含 |
| `tests_pass` / `syntax_pass` | 子 Agent 时间线里的真实验证证据 |

规则：

- 命令/API 默认关闭，调用方必须显式 `allow_commands=true`；
- `high` / `critical` 风险任务没有确定性证据时直接失败；
- 自然语言验收标记 `manual`，不会算作 `verified`；
- 没有机器可检查条件时 `verified=false`，明确区分“文本完成”和“验证完成”。

独立验证：

```python
await independent_review(task, result, workspace, run_subagent)
```

它先跑确定性检查，再让一个只读子 Agent 打开实际文件核对；独立结论只能补充/否定，
不能推翻确定性失败。`task_run(independent=true)` 可启用。

## Benchmark

`tests/evals/tasks.json` 已有 30 条固定任务，覆盖：

- 简单工具；
- 多步骤文件；
- 多文件代码修改；
- 调试；
- 项目分析/报告；
- 网络搜索；
- 长上下文；
- 上下文压缩；
- 失败恢复；
- 多 Agent 并行；
- 安全边界；
- 模糊需求规划。

运行：

```powershell
.\.venv\Scripts\python.exe scripts\eval.py `
  --model kimi-coding/kimi-for-coding --task-timeout 180 --compare
```

报告写入 `data/evals/report-<timestamp>.json`，包含：

```text
metrics:
  完成率 / 验收率
  平均耗时 / 平均步数 / 工具调用
  平均重试 / 无进展次数
  子 Agent 调用/成功率/返工率
  token / 压缩恢复
comparison:
  与上一份报告逐指标 before/after/delta
```

报告不再只看单测通过率；每条任务的 `checks` 是独立断言。

## 已知限制

- `subagent_rework_rate` 依赖子 Agent 结果里的返工事件；普通 `subagent` 调用不提供，
  当前可能为 0/None；
- 成本只在服务返回 `usage` 时才有；
- 压缩恢复指标需要任务实际 rollover/compact，普通运行可能为 None；
- 30 条任务全量跑一次会消耗真实模型额度，建议按 `--only` 分批。
