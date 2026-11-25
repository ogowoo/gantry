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

### [x] AUD-P0-01 fileview 绝对路径任意文件读（审核 ④-1，实测复现）

- 问题：`fileview._resolve` 无 resolve+relative_to 校验，`C:/Windows/win.ini` 等绝对路径直接读。
- 文件：gantry/fileview.py、tests/。
- 实施：`(root/t).resolve()` + `relative_to(root.resolve())`，拒绝盘符/绝对路径。
- 验收：新增绝对路径/`..` 越界回归用例；全部旧用例通过。

### [x] AUD-P0-02 write_excel 写锁跨 await 死锁（审核 ⑥-1）

- 问题：`threading.Lock` 在 async 函数内跨 `await asyncio.to_thread` 持有，并发可永久卡死服务。
- 文件：gantry/tools_office.py。
- 实施：`_WRITE_LOCK` 改 `asyncio.Lock`，两处改 `async with`。
- 验收：并发 write_excel/write_csv 用例 + 全量回归。

### [x] AUD-P0-03 全新安装 api_token 占位符失效（审核 ⑦-1）

- 问题：config 里 `${GANTRY_TOKEN}` 未解析/空值被当成"已配置"，鉴权失效或成公开常量。
- 文件：start.py、tests/。
- 实施：含未解析 `${...}` 或解析后为空 → 视为未配置，生成 data/api_token。
- 验收：无 .env / 空 .env / 正常 .env 三用例。

### [x] AUD-P0-04 detect-models 凭据外送到调用方 URL（审核 ③-1）

- 问题：`api_key_env`/已保存 provider 凭据被发到请求体指定的任意 base_url。
- 文件：gantry/routes_providers.py、tests/。
- 实施：已保存凭据仅在 base_url 同源时回退；env 名只允许该 provider 已配置项。
- 验收：跨 URL 外送用例返回 400。

### [x] AUD-P0-05 MUTATING_TOOLS 名单补齐（审核 ④-2/①-1）

- 问题：read-only/plan_mode 只按工具名拦截，write_csv/skill_create/memory_forget/self_*/copilot_file/run_code 等可绕过。
- 文件：gantry/tools.py、tests/test_sandbox_modes.py。
- 实施：补全名单；补表驱动测试（每个写工具在 read-only 下抛 SANDBOX_READ_ONLY）。
- 验收：表驱动测试通过。

### [x] AUD-P0-06 发布包自包含 + 更新器不删未知资产（审核 ⑨-1/⑦-3）

- 问题：zip 包缺 AGENT.md/skills/personas/tests/可选 requirements；updater 对"新包不存在"的旧条目 rmtree。
- 文件：.github/workflows/release.yml、gantry/updater.py、tests/test_updater.py。
- 实施：打包清单补齐；未知顶层项保留；plugins/skills/personas 按子项合并替换。
- 验收：test_updater 增加"未知顶层项与用户插件存活"用例。

### [x] AUD-P0-07 kb_add 任意路径读取（审核 ⑤-1）

- 问题：绝对路径读本机任意文件入 KB，绕过沙箱与审批。
- 文件：plugins/kb/plugin.py、gantry/tools.py。
- 实施：路径收口到会话工作区；注册 approval=True 并加入 MUTATING_TOOLS。
- 验收：越界路径用例 + read-only 档位用例。

### [x] AUD-P0-08 task_run 网页通道并行子代理串台（审核 ②-1）

- 问题：子代理继承父 `_PAGE_CTX`，同一标签页并发 new_chat/输入/取答。
- 文件：gantry/orchestrator.py。
- 实施：网页通道按 `_subagent_in_own_window` 模式清 `_PAGE_CTX` 自占窗口；窗口不足 cap=1。
- 验收：网页通道并行任务取窗口的用例。

### [x] AUD-P0-09 teams-bot send-api 绕过 dry_run 门控（审核 ⑥-3）

- 问题：`/api/send-api`、`/api/compose?send=true` 直接发送，绕过 dry_run/send_enabled。
- 文件：plugins/teams-bot/plugin.py。
- 实施：两个端点接入与 `_teams_reply` 相同的门控判定。
- 验收：dry_run 下拒绝真实发送的用例。

### [x] AUD-P0-10 MCP 命令白名单 args 绕过（审核 ⑦-4）

- 问题：python args 传 `-c` 即任意代码执行并持久化。
- 文件：plugins/mcp/plugin.py。
- 实施：python 仅 `-m`；node 禁 `-e/--eval`；npx 仅允许登记的包名。
- 验收：恶意 args 400 用例。

### [x] AUD-P0-11 xlsx_patch 自闭合 &lt;row/&gt; 吞行（审核 ⑥-2）

- 问题：ROW_RE 跨自闭合行匹配，写第 5 行损坏文件/写第 6 行被静默忽略。
- 文件：gantry/xlsx_patch.py、tests/test_xlsx_patch.py。
- 实施：自闭合行单独匹配保留；完整行禁止跨 &lt;row 匹配。
- 验收：自闭合行/跨行/末尾插入回归用例。

### [x] AUD-P0-12 审批横幅盲签重做（审核 ⑧-1）

- 问题：不显示参数、主按钮默认整会话放行、"暂不授权"无限等待。
- 文件：gantry/agent.py、gantry/routes_sessions.py、webui/js/approvals.js。
- 实施：参数摘要展示；主按钮「仅本次」；会话级二次确认；新增拒绝入口；尊重 approval_scope。
- 验收：后端 args_summary 用例 + node --check + cross_module_refs。


### [x] AUD-P3-01 KB 向量生命周期与 embedding 配置链路（审核 ⑤-3/⑤-4）

- 问题：chunk 原地改文复用旧向量；孤儿向量占满 top-k；stats 误报 complete；plugin.json 的 embedding/ocr 覆盖 config.json；hybrid 失败直接抛错；local 后端只认 model_path。
- 文件：gantry/kb.py、gantry/kb_semantic.py、plugins/kb/plugin.py、plugins/kb/plugin.json。
- 实施：向量行加 text hash（懒迁移）逐行比对；reindex 清理孤儿向量；检索 JOIN chunks；stats 真实覆盖；_emb_cfg 签名缓存自动重建；plugin.json 只留非 embedding 默认；local 接受 model 路径；hybrid 失败退回 FTS。
- 验收：原地改文重算、删除文档清理两个回归用例 + KB 全量测试。

### [x] AUD-P3-02 浏览器/webchat 并发与恢复（审核 ①-4/①-5/①-7）

- 问题：浏览器启动无互斥、忙判定漏空 sid 占用、标签页被关后重试无法自愈、webchat 同站并发串台。
- 文件：gantry/browser.py、gantry/window_pool.py、gantry/webchat.py。
- 实施：_START_LOCK 包住启动全程；Window.occupied 独立占用标记 + any_occupied；复用前探测页面存活并重建；webchat 每站点 asyncio.Lock 覆盖建页+发送+等待。
- 验收：window_pool/webchat/browser 相关测试全绿。

### [x] AUD-P3-03 流式等待与前端收口（审核 ①-9/⑧-5/⑧-9/⑧-11/⑨-7/⑨-10）

- 问题：180s 墙钟误杀长回答；sessions/queue 轮询无 in-flight 防护；i18n 翻译缓存用首次快照；死代码；CHANGELOG 重复版本块；CI 无 JS 语法门。
- 文件：gantry/page.py、webui/js/sessions.js、composer.js、i18n.js、settings.js、themeboot.js、CHANGELOG.md、.github/workflows/ci.yml。
- 实施：idle-deadline（持续出字等到硬上限 1200s，无文本按 180s；稳定无信号按 180s 收尾）；轮询 in-flight；i18n 缓存随内容变化刷新；删 config 树编辑器等死代码；CHANGELOG 去重 234 行；CI 加 node --check。
- 验收：node --check 全量、cross_module_refs、ESM/i18n 测试通过。

### [x] AUD-LIVE-01 真机实测：KB 向量全量重建 + 重启链路（2026-11-25 晚）

- 结果：local bge-m3 嵌入 23/23 块 complete=true；embedding 配置链路修复生效；
  重启后登录态保持、liveview 正常。裸文件名 model 的路径回退已补（ff0d5ea）。
- 待办：Copilot 超限窗口恢复后补测只读任务/fileview 绝对路径/@引用（挂起任务 bad38gviv 观察中）。

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

### [x] AUD-P1-01 验证门/行动检测漏 apply_patch 等推荐编辑工具（审核 ②-2）

- 问题：apply_patch/replace_lines/replace_python_symbol/convert_indentation/indent_lines/pwsh/write_csv 改 .py 不触发 validate+unittest 双门，且 noop 守卫误判"无产出"。
- 文件：gantry/agent.py、tests/。
- 实施：按 file_event.path 扩展名统一判定，与 MUTATING_TOOLS 同源。
- 验收：apply_patch→final 被 validation_gate 拦截的回归用例。

### [x] AUD-P1-02 截断 final 兜底抢在写入恢复之前（审核 ②-3）

- 问题：截断写入内容含 final 字面量时被误解析为 final，写入丢弃却报完成。
- 文件：gantry/agent_parse.py、tests/test_agent_parse.py。
- 实施：加 `not outer_broken_tool` 守护并移到写入恢复之后。
- 验收：新增回归用例。

### [x] AUD-P1-03 子代理非成功态被当成功（审核 ②-4）

- 问题：waiting/suspended/running 出口一律 patch completed 且 error=None。
- 文件：gantry/subagents.py、gantry/agent.py。
- 实施：只认显式成功态；发送失败 3 次出口置 failed。
- 验收：等待态子任务→error 透传用例。

### [x] AUD-P1-04 H3 残留：验收命令 env 继承 + stdout 未脱敏（审核 ②-9）

- 问题：子进程继承完整环境，stdout/stderr 原样回灌模型并落库。
- 文件：gantry/task_verify.py、tests/test_task_verify.py。
- 实施：env 最小白名单；stdout/stderr 同样 _redact_secrets。
- 验收：脱敏用例 + env 白名单用例。

### [x] AUD-P1-05 H4 残留：子代理验收门读默认 tasks.db（审核 ④-4）

- 问题：subagents final_gate 未传 store，自定义 task_db_path 时验收门静默跳过。
- 文件：gantry/subagents.py。
- 实施：传 `b.TOOLS_EXTRA._task_store()`。
- 验收：自定义 task_db_path + 子代理用例。

### [x] AUD-P1-06 插件 name 路径越界（审核 ⑦-2）

- 问题：install/uninstall 的 name 未校验，可对插件目录外 rmtree/copytree。
- 文件：gantry/plugins.py、tests/。
- 实施：`^[A-Za-z0-9_.-]+$` + resolve().relative_to 双路径校验。
- 验收：绝对路径/`..` name 用例。

### [x] AUD-P1-07 定时任务导入模式残留（审核 ⑧-2）

- 问题：覆盖导入取消后 dataset.mode 残留，下次合并导入静默清空任务。
- 文件：webui/js/panels.js。
- 实施：每次打开选择器前显式设置本次模式。
- 验收：node --check。

### [x] AUD-P1-08 kb _root(ctx) TypeError（审核 ⑤-5）

- 问题：`_root()` 定义无参却被 `_root(ctx)` 调用，相对路径导入必失败、OCR 磁盘配置失效。
- 文件：plugins/kb/plugin.py。
- 实施：修正调用；相对路径按会话工作区解析。
- 验收：相对路径导入用例。

### [x] AUD-P1-09 teams-bot api/reindex NameError（审核 ⑥-9）

- 问题：调用不存在的 `_kb_reindex`，端点必然 500。
- 文件：plugins/teams-bot/plugin.py。
- 实施：改为 `_kb(ctx).reindex(force=False)`。
- 验收：端点不抛 NameError 的用例。

### [x] AUD-P1-10 导入 conversation_urls 绕过删除白名单（审核 ③-2）

- 问题：被篡改导出的 URL 驱动 SSO 浏览器导航任意地址。
- 文件：gantry/routes_sessions.py。
- 实施：删除前统一过 `_copilot_delete_urls` 白名单。
- 验收：非 Copilot host URL 被拒用例。

### [x] AUD-P1-11 运行中会话本地删除无守卫（审核 ③-3）

- 问题：不检查 running 就删，runner 继续写孤儿行、级联删子会话。
- 文件：gantry/routes_sessions.py。
- 实施：仅本地删除也 session_running（含后代）检查并 409。
- 验收：运行中删除 409 用例。

### [x] AUD-P1-12 CSRF 白名单顺序 + 非 ASCII token 500（审核 ③-9）

- 问题：白名单 origin 带 cross-site 仍 403；compare_digest 遇非 ASCII 抛 TypeError。
- 文件：gantry/api.py、tests/test_api_hardening.py。
- 实施：先判 allow_extra 再判 Sec-Fetch-Site；digest 前 encode。
- 验收：两组回归用例。

### [x] AUD-P1-13 单实例锁（审核 ⑦-7）

- 问题：不同端口第二实例把第一实例会话置 paused 并可能并发续跑。
- 文件：start.py。
- 实施：data/.instance.lock 独占创建，第二实例拒绝。
- 验收：锁占用时启动失败用例。

### [x] AUD-P1-14 更新锁进程级（审核 ⑦-6）

- 问题：HTTP 每请求新建 Updater，实例级锁形同虚设。
- 文件：gantry/routes_update.py、gantry/updater.py。
- 实施：进程级锁包住 apply/rollback。
- 验收：并发 apply/rollback 用例。

### [x] AUD-P1-15 事件循环阻塞清单（审核 ③-5/③-6/⑤-8/①-10/②-8）

- 问题：OAuth 刷新/30s 巡检/模型库列表/atrefs/验收门同步重活冻结事件循环。
- 文件：gantry/oauth.py、gantry/api.py、gantry/routes_config.py、gantry/atrefs.py、gantry/task_verify.py。
- 实施：逐一 to_thread / light 查询 / 剪枝。
- 验收：静态确认无 async 路径同步 IO（清单复查）。

### [x] AUD-P1-16 依赖清单漂移（审核 ⑨-4）

- 问题：pyproject 缺 pyyaml 等运行依赖、pypdf 约束与 lock 冲突。
- 文件：pyproject.toml、requirements.txt、.github/workflows/ci.yml。
- 实施：pyproject 补运行依赖并统一 pypdf；CI 加一致性检查。
- 验收：pip check + 一致性检查通过。

### [x] AUD-P2-01 顺手低危项（审核 P2 清单）

- 问题：token 弹窗循环、OWA 发送假成功、open_teams 劫页、owa pop 空值、队列无背压、b.log 丢失、lazy pending 归档、发布门 skip 白名单、超长段硬切、_load_lock。
- 文件：见审核报告 P2 清单。
- 实施：逐项最小修复，带用例。
- 验收：相关回归用例通过。

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

### [ ] P1-CI-02 查清 test_liveview 在 GitHub Windows runner 上挂死

- 现象：`run_tests_chunked.py` 里 `test_liveview` 在本地 1s 通过（全部用假页面 /
  假 CDP / mock，无真实浏览器），但在 GH hosted Windows runner 上稳定跑满
  180s 被判 TIMEOUT，直接拖挂 Trusted Release（v0.12.0-beta.34 的发布失败）。
- 当前处置：release.yml 用 `--skip test_liveview` 先移出发布门（浏览器相关覆盖
  仍由 `browser-integration.yml` 提供），避免发布流水线被单模块拖死。
- 待查：在 CI 里加 `faulthandler.dump_traceback_later(120)` 或按测试方法粒度打印
  进度，定位卡在哪个用例（怀疑 `asyncio` 事件循环 + `loop.call_later` 回调在
  无人值守会话下的行为差异）；定位后再把该模块加回发布门。
- 验收：CI 上 `test_liveview` 单独跑通（或确实证明是环境限制并加明确 skip 条件），
  release.yml 去掉 `--skip test_liveview`。
- 依赖：无。

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
