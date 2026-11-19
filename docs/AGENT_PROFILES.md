# Agent 领域 Profile

日期：2026-11-19

Gantry 的 Agent 不再只靠“通用系统提示词”。四个高频方向现在有轻量 profile：

| profile | 触发关键词（示例） | 注入规则 | 展开工具组 |
|---|---|---|---|
| `power_automate` | Power Automate、云端流、审批流、自动化流 | 先生成包、校验、UI 只做导入/验证 | `power_automate` |
| `power_apps` | Power Apps、canvas app、画布应用、模型驱动 | 先生成 YAML source、pac/Studio 导入 | 插件工具（未分组工具本就常驻） |
| `office365` | Excel、Word、PPT、PDF、Outlook、Teams、报表 | 读后写、保格式、重开验证、发信确认 | `office`、`outlook` |
| `coding` | 编程、代码、Python、重构、修复、测试 | 先读后改、最小改动、验证闭环 | `python`、`notebook` |

## 自动与强制

默认：

```json
"agent": {"profile": "auto"}
```

- `auto`：按任务关键词自动选一个；无命中则不注入
- `off`：完全关闭领域 profile
- `power_automate` / `power_apps` / `office365` / `coding`：强制使用该 profile

强制示例：

```json
"agent": {"profile": "office365"}
```

## 两层结构

```text
系统提示词
  └─ profile 硬约束（短：5 条左右）
       └─ skill 详细流程（按需 skill_read）
```

- profile 只做**路由和硬约束**，避免每轮塞大段教程；
- 详细步骤在 `skills/<name>/SKILL.md`：`power-automate`、`power-apps`、`office365`、`coding`；
- profile 是提示词路由，不是权限边界：审批、沙箱、allow_* 仍然生效。

## 为什么不把完整教程塞进系统提示词

- 每轮都注入会显著增加 token；
- 教程与任务无关时反而干扰模型；
- skill 是渐进式披露：模型先看到一句描述，相关时再 `skill_read` 读全文。

## 推荐任务写法

```text
// Power Automate
用 Power Automate 做一个“新邮件到达 → 写入 SharePoint”的流，生成导入包并校验。

// Power Apps
创建一个 canvas app：首页一个标题和按钮，生成 source 并说明导入步骤。

// Office 365
读取 Q3.xlsx，保留公式和样式，在 Summary 表加一列“环比”，写完读回验证。

// Coding
修复 tests/test_api.py 的失败用例；先定位原因，最小改动，跑 validate_python + run_unittest。
```

Profile 会自动命中；如果误判或想强制，改 `agent.profile`。

## 维护

- 新增方向：在 `gantry/agent_profiles.py` 加 `Profile`，并补 `TOOL_GROUPS`（只放**静态核心工具**；插件动态工具不要写进静态组，否则不变量会报错）。
- 详细流程写 `skills/<name>/SKILL.md`，不要写进 profile。
- 测试：`tests/test_agent_profiles.py`、`tests/test_prompt_stability.py`。
