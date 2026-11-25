# Gantry 治理 TODO

## 优先级定义

- P0：安全边界、主线质量门或发布阻断项。
- P1：可靠性、资源生命周期、状态一致性和运维能力。
- P2：工程优化、可维护性和文档治理。

## 建议执行顺序

- 安全边界 → 资源与状态 → 测试与 CI → 供应链与发布 → 可观测性与文档。

## 已具备能力

- [x] 高风险能力默认关闭。
- [x] 工作区路径约束、审批、快照和回滚。
- [x] Python 语法检查与 unittest 验证门。
- [x] Agent 解析、API 加固、验证门和工作区隔离专项测试。

## P0

### [ ] P0-SEC-01 非 loopback 强制 Token

- 问题：非回环绑定未确认强制认证。
- 影响：可能远程暴露高权限 API。
- 文件：start.py、gantry/api.py、config.example.json。
- 实施：Token 为空时拒绝非 loopback 启动。
- 验收：回环地址可无 Token 启动；非回环无 Token 必须失败；配置组合测试通过。
- 依赖：无。

### [x] P0-CI-01 建立 CI 必过门

- 问题：已将确定性离线测试与浏览器自动化测试拆分，避免默认 CI 被外部浏览器状态拖住。
- 影响：主线使用稳定离线门禁，浏览器集成测试在独立任务中按明确超时运行。
- 文件：.github/workflows/ci.yml、.github/workflows/browser-integration.yml、tests/、scripts/。
- 实施：默认 CI 执行语法检查与离线 unittest；浏览器集成工作流使用 Windows 专用任务、独立窗口和明确超时。
- 验收：仓库语法检查通过；a-l、m-r、s-z 离线测试组均以 exit_code=0 通过；test_copilot_file.py 与 test_model_menu_scan.py 通过。
- 依赖：P1-TST-01（已完成）。

### [ ] P0-SUP-01 锁定依赖并生成 SBOM

- 问题：未确认传递依赖锁定、哈希校验和 SBOM。
- 影响：安装漂移及供应链风险。
- 文件：pyproject.toml、requirements.txt、install.py。
- 实施：锁定依赖、漏洞扫描、许可证扫描及 SBOM。
- 验收：干净环境安装可复现，发布制品包含 SBOM。
- 依赖：无。

### [x] P0-REL-01 建立可信发布流水线

- 问题：已建立 Tag 或手工触发的可信发布流水线。
- 影响：发布制品现在具备可验证的完整性、依赖清单与来源证明。
- 文件：.github/workflows/release.yml、docs/VERSIONING.md、CHANGELOG.md。
- 实施：执行版本校验、锁定依赖安装、源码编译、完整测试、漏洞与许可证扫描、SBOM、归档、SHA-256 校验和、产物校验、来源证明及 GitHub Release 发布。
- 验收：所有门禁失败关闭；缺失或空制品会阻止上传，正式发布使用 --verify-tag。
- 依赖：P0-CI-01、P0-SUP-01（已完成）。

## P1

### [x] P1-RES-01 修复 SQLite ResourceWarning

- 问题：已修复主要 SQLite 测试夹具和运行路径中的连接释放缺陷。
- 影响：降低长期运行时连接句柄累积和测试退出阶段告警风险。
- 文件：gantry/session_store.py、gantry/tools.py、gantry/invariants.py、测试清理路径。
- 实施：明确连接所有权，使用受管连接并补齐 close() 协议。
- 验收：仓库语法检查通过，离线测试三组全部通过；部分独立测试仍观察到零星 ResourceWarning，列为后续清理项。
- 依赖：无。

### [ ] P1-STA-01 统一状态提交入口

- 问题：SQLite、内存、SSE、浏览器和 WebUI 存在多状态源。
- 影响：可能出现竞态、过期审批或状态漂移。
- 文件：gantry/agentrun.py、gantry/session_store.py、相关路由和 WebUI。
- 实施：定义状态机、合法迁移、幂等操作和状态版本。
- 验收：暂停、恢复、审批、终止和重启场景测试通过。
- 依赖：P1-RES-01。

### [ ] P1-TST-01 解决完整测试超时并实施分层

- 问题：完整 unittest 超时，当前只能确认关键专项测试通过。
- 影响：无法宣称全量回归通过。
- 文件：tests/、CI 工作流。
- 实施：拆分 fast、integration、browser、release 并设置独立超时。
- 验收：各层可独立稳定完成并输出报告。
- 依赖：无。

### [ ] P1-REL-02 建立恢复演练

- 问题：已有多条回滚通道，但需持续验证。
- 影响：灾难场景中的恢复时间不确定。
- 文件：gantry/snapshots.py、scripts/rollback.py、tests/。
- 实施：自动演练新增、修改、移动、删除和 revision 回滚。
- 验收：记录 RTO、RPO 和不可回滚边界。
- 依赖：P0-CI-01。

### [ ] P1-CFG-01 引入严格配置 Schema

- 问题：类型、范围、未知字段和危险组合需统一校验。
- 影响：错误配置可能延迟到运行时暴露。
- 文件：config.example.json、gantry/routes_config.py、启动逻辑。
- 实施：定义 Schema、重启要求和安全影响。
- 验收：错误类型、越界值和危险组合均被拒绝。
- 依赖：支撑 P0-SEC-01。

### [ ] P1-CFG-02 统一窗口数量上限

- 问题：窗口范围在多个模块和文档中不一致。
- 影响：配置校验与运行行为不一致。
- 文件：gantry/browser.py、gantry/routes_config.py、gantry/window_pool.py、docs/MULTI-WINDOW.md。
- 实施：确定唯一范围并同步代码、配置和文档。
- 验收：最小值、最大值和越界测试通过。
- 依赖：P1-CFG-01。

### [ ] P1-OBS-01 结构化日志、脱敏与指标告警

- 问题：未确认统一 JSON 日志、correlation ID、指标和告警规则。
- 影响：故障发现和定位较慢。
- 文件：API、Agent、Provider、工具层和 docs/。
- 实施：统一日志字段与脱敏；输出延迟、错误率、队列、任务、重试和窗口指标。
- 验收：敏感信息不落盘，关键异常可触发告警。
- 依赖：P1-STA-01。

### [ ] P1-OBS-02 拆分存活与就绪探针

- 问题：健康状态可能混合进程、浏览器和外部服务状态。
- 影响：编排系统难判断是否可接收请求。
- 文件：gantry/routes_core.py、gantry/api.py、tests/。
- 实施：增加 /health/live 与 /health/ready。
- 验收：不同故障场景返回明确状态码。
- 依赖：P1-OBS-01。

## P2

### [ ] P2-DOC-01 补齐 API、配置、安全和故障排查文档

- 问题：现有文档未完整覆盖路由、配置边界、安全模型和常见故障。
- 影响：使用与运维依赖阅读源码。
- 文件：docs/API.md、docs/CONFIGURATION.md、docs/SECURITY.md、docs/TROUBLESHOOTING.md。
- 实施：按代码证据生成参考文档并纳入更新检查。
- 验收：用户无需阅读源码即可配置、调用和定位常见问题。
- 依赖：P1-CFG-01、P1-OBS-02。

### [ ] P2-ARC-01 降低 DOM 与工具裁剪脆弱性

- 问题：浏览器通道依赖外部 DOM，工具可见性依赖关键词。
- 影响：页面更新或表达变化可能导致功能失效。
- 文件：gantry/browser.py、gantry/page.py、gantry/tools.py。
- 实施：选择器契约测试、诊断降级和按需工具发现。
- 验收：关键 DOM 变化可快速检测；必要工具可动态发现。
- 依赖：P0-CI-01。

### [ ] P2-UX-01 拖拽附件：把文件拖进对话页即复制到会话工作区

- 来源：2026-11-25 用户反馈「任务要统计工作区里的 Tracker excel，但文件不在工作区」。
  当前附件只支持**粘贴图片**（`data/uploads`，仅随消息发给 Copilot），没有通用文件上传；
  用户以为"在界面上选了文件就已经挂载"，实际 agent 的文件工具只看得到会话工作区。
- 目标：对话页支持拖拽/选择任意文件（xlsx、csv、pdf、代码…）→ 复制进会话工作区
  （或 `workspace/<sid>/attachments/` 并写进提示词），任务里 agent 能直接用 `read_excel`
  / `read_text`；同时给出"已放入 <绝对路径>"的回执。
- 文件：webui/js/composer.js（拖拽 + 上传）、gantry/routes_core.py（上传落盘 + 大小/类型白名单）、
  gantry/agentrun.py（落盘目录与提示词）、gantry/agent_prompt.py（告知附件位置）。
- 验收：拖一个 .xlsx 进对话页 → 新任务里 `list_directory` 能看到它、`read_excel` 能读；
  超限/危险扩展名（.exe/.bat）被拒绝并提示；不覆盖工作区同名文件（改名或确认）。
- 依赖：P1-AGENT-01（先确认 agent 确实工作在会话工作区，否则上传了也看不到）。
- 备注：用户 2026-11-25 明确"先不加"，等确认工作区问题后再做。

### [ ] P1-AGENT-01 查清"模型只看到 2 个文件"的真实原因（工作区 vs Copilot 沙箱 / 线程记忆）

- 现象（2026-11-25 异机）：会话工作区显示 `C:\gantry\workspace\Split Tracker`，
  用户确认目录里**有 xlsx**；但 agent 的 `list_directory` 结果只有
  `_validate_okam_inputs.py` + `__pycache__/…cpython-312.pyc`，回答是 Copilot 风格的
  文件表格，并臆造"文件出现在 `/mnt/data` 就能继续"。用户怀疑"用久了 Copilot 记住了
  我的操作形式，所以我能成功"。
- 待查分支（按可能性）：
  1. **工具根与会话工作区不一致**：`run_agent` 用 `req.workspace or 既有 workspace or
     DEFAULT_WORKSPACE` 后 `T.set_root(...)`；需在出事机器上核对 `GET /v1/agent/sessions/<sid>`
     的 `workspace`、步骤时间线里 `list_directory` 的 `arguments.path` 与返回的
     `workspace_root`（beta.33 起结果里带绝对根），确认模型列的是哪个目录；
  2. **模型没走本地工具**：回答卡片是 Copilot 生成的（Copilot 在自己的 `/mnt/data`
     沙箱里 listdir），即这一轮实际是"普通对话/委托给 Copilot"，不是本地工具调用；
     核对该步骤是否 `tool=list_directory`、是否有本地工具结果；
  3. **Copilot 线程/长期记忆**：同一 Copilot 会话线程累积上下文（以及 memory_write
     写入的记忆、skills）会让"老会话"表现更好，新会话/换机器就退化——需要记录
     会话线程 ID 与新老会话对照实验。
- 验收：在出事机器上用上面三条证据定位到唯一原因，并补一条回归测试或最小修复；
  若属 Copilot 侧行为，则写进 docs 与提示词（"不要引用 Copilot 沙箱路径"）。
- 依赖：无（beta.33 已提供 `workspace_root` 锚点，便于取证）。

## 里程碑

- M1：关闭全部 P0。
- M2：修复 SQLite、状态一致性和测试超时。
- M3：完成可信发布、恢复演练和可观测性。
- M4：完成文档与架构优化。

## 完成定义

- 对应代码、测试和文档全部提交。
- validate_python 通过。
- 分层 unittest 门禁通过；未通过或超时必须明确记录。
- 安全、回滚和配置边界有自动化验收。
- TODO 项的证据、依赖和验收结果可追溯。
