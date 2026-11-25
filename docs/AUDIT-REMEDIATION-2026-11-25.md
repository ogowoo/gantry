# 审核整改方案（2026-11-25 全面审核）

> 依据：2026-11-25 全面审核报告（9 审核面、99 条发现）。本文件是整改方案的唯一详述；
> 执行状态跟踪在 `docs/TODO.md`（条目编号 `AUD-P0-xx / AUD-P1-xx / AUD-P2-xx`）。

## 执行状态（2026-11-25 完成本批）

- ✅ P0 全部 12 项落地（AUD-P0-01 … AUD-P0-12），每项带回归测试或针对性用例；
- ✅ P1 全部 16 项落地（AUD-P1-01 … AUD-P1-16；P1-16 含 CI 依赖一致性检查步骤）；
- ✅ P2 顺手项（AUD-P2-01）：OWA 发送验证与 pop 空值、open_teams 不劫共享页、
  队列背压、b.log 接入 logging、lazy 模式 pending 归档、token 弹窗循环、
  超长段硬切、_load_lock、发布门 skip 收紧。
- ✅ 门禁：`py_compile` 全量通过、`cross_module_refs` 通过、36 个 JS `node --check`
  通过、相关测试模块全部通过；全量分块测试见最后一次运行结果。
- ✅ 第二轮清尾（用户"还有没修复的吗"）：②-5 批量结果回灌、②-6 回合中取消
  （interrupt_check 并入 cancel + 每动作执行前复查）、②-10 解析失败累计上限、
  ②-11 goal 续轮状态顺序、②-12 批量回答等待快路径、③-5 test_provider 改
  AsyncClient、③-6 快照 diff to_thread、⑤-7 vector-index limit 不参与 prune、
  ⑥-7 pac 白名单、⑥-8 pa_import allow_live_import、M15 发送取消草稿清理、
  M24 page.visible try 兜底、①-11 WindowPool 摘除顺序、⑦-10 检查失败语义
  （check_failed + 前端提示）、⑧-4 动态区 i18n、⑧-7 消息按 id 判重、
  ⑧-8 OAuth 链接 https 校验、⑧-10 cross_module_refs globals 同名盲区、
  ⑨-6 run_tests_chunked skip 校验。
- 仍未纳入（保持 TODO 跟踪，属结构性/大改项）：KB 向量生命周期与 embedding
  配置链路重构（⑤-3/⑤-4，需数据迁移方案）、Teams/OWA 大重构项（①-4/①-5/
  ①-6/①-7/①-8/①-9 浏览器 DOM 层恢复与互斥）、WebUI 轮询 in-flight 与懒构建
  （⑧-5/⑧-6）、i18n 翻译缓存（⑧-9）、死代码清理（⑧-11）、CHANGELOG 去重
  （⑨-7）、前端质量门进 CI（⑨-10）。

## 执行原则

1. 每项修复带回归测试（安全类修复必须有"防回退"用例）。
2. 每批改完跑 `validate_python` + 相关单测；全部完成后跑分块全量测试。
3. 不扩大改动范围：只修审核发现的问题；发现无关问题只记录不顺手改。
4. 顺序：安全边界 → 数据完整性 → 状态机正确性 → UI → 测试/CI。

## P0（12 项）

| # | TODO 编号 | 审核发现 | 位置 | 改法要点 | 验收 |
|---|---|---|---|---|---|
| 1 | AUD-P0-01 | ④-1 fileview 任意文件读 | gantry/fileview.py:62-80 | `_resolve` 统一 `(root/t).resolve()` + `relative_to(root.resolve())`，拒绝盘符/绝对路径/越界 | 新增绝对路径/`..` 用例；旧用例全过 |
| 2 | AUD-P0-02 | ⑥-1 write_excel 写锁死锁 | gantry/tools_office.py:35,696,925 | `_WRITE_LOCK` 改 `asyncio.Lock`，两处改 `async with` | 并发写用例；全量回归 |
| 3 | AUD-P0-03 | ⑦-1 api_token 占位符 | start.py:98-101 | 含未解析 `${...}` 或解析后为空 → 视为未配置，走生成 `data/api_token` | 无 .env/空 .env/正常 .env 三用例 |
| 4 | AUD-P0-04 | ③-1 detect-models 凭据外送 | gantry/routes_providers.py:347-399 | 已保存凭据仅在 base_url 与已存 provider 同源时回退；`api_key_env` 只允许该 provider 已配置的 env 名（未传 name 时拒绝） | 跨 URL 外送用例 400 |
| 5 | AUD-P0-05 | ④-2/①-1 沙箱名单缺口 | gantry/tools.py:195-202 | MUTATING_TOOLS 补 `write_csv/skill_create/memory_forget/self_write_text_b64/self_append_text_b64/self_replace_text/copilot_file/run_code/teams_reply/pa_import/power_apps_generate/power_apps_pack` | 表驱动测试：每个写工具在 read-only 下抛 SANDBOX_READ_ONLY |
| 6 | AUD-P0-06 | ⑨-1/⑦-3 发布包/更新器删资产 | .github/workflows/release.yml:137、gantry/updater.py:496-498,628-637 | 打包清单补齐 AGENT.md/skills/personas/tests/pyproject.toml/可选 requirements/多语 README；updater 对"新包不存在"的旧条目改为保留（只替换新包包含的条目）；plugins/skills/personas 按子项合并替换 | test_updater 增加"未知顶层项与用户插件存活"用例 |
| 7 | AUD-P0-07 | ⑤-1 kb_add 任意读 | plugins/kb/plugin.py:382-405 | 绝对路径仅允许在会话工作区内（ctx 提供根时校验 relative_to）；否则报错要求相对路径；注册 `approval=True` 并加入 MUTATING_TOOLS | kb_add 越界路径用例 + read-only 档位用例 |
| 8 | AUD-P0-08 | ②-1 task_run 网页并行串台 | gantry/orchestrator.py:242 | 网页通道（父 `_PAGE_CTX` 非空）时：每个 executor=subagent 的任务在自身上下文清空 `_PAGE_CTX` 自占窗口（复用 tools_extra._subagent_in_own_window 模式）；窗口不足时并发 cap=1 | 网页通道并行任务取窗口的测试（mock 窗口池） |
| 9 | AUD-P0-09 | ⑥-3 teams-bot send-api 绕过 dry_run | plugins/teams-bot/plugin.py:1019-1070 | 两个端点接入与 `_teams_reply` 相同的 dry_run/send_enabled/allow_force_send 判定 | 端点在 dry_run 下拒绝真实发送的用例 |
| 10 | AUD-P0-10 | ⑦-4 MCP args 白名单绕过 | plugins/mcp/plugin.py:679-692 | HTTP 接口：python 只允许 `-m`（禁 `-c/-u/--` 任意）；node 禁 `-e/--eval`；npx 仅允许插件配置登记的包名；args 校验拒绝含 `;|&` 等 | add 端点恶意 args 400 用例 |
| 11 | AUD-P0-11 | ⑥-2 xlsx_patch 自闭合行 | gantry/xlsx_patch.py:35,190-197 | ROW_RE 拆两个模式（自闭合行单独匹配、完整行用 tempered dot 禁止跨 `<row`）；写入/插入逻辑适配；保留自闭合行 | 自闭合行/跨行/末尾插入回归用例 |
| 12 | AUD-P0-12 | ⑧-1 审批横幅盲签 | gantry/agent.py:1396-1403、gantry/routes_sessions.py:749-760、webui/js/approvals.js | 后端 approval_pending 带脱敏参数摘要；UI 主按钮改「仅本次」（scope=once），会话级放行需二次确认；新增「拒绝并取消」端点与按钮；尊重 approval_scope | 前端静态检查 + 后端 args_summary 字段用例 |

## P1（高价值，16 项）

| # | TODO 编号 | 审核发现 | 位置 | 改法要点 |
|---|---|---|---|---|
| 13 | AUD-P1-01 | ②-2 验证门/行动检测漏 apply_patch 等 | gantry/agent.py:573-578,1503-1506 | 按 `file_event.path` 扩展名判断（或统一"代码写入工具"集合），覆盖 apply_patch/replace_lines/replace_python_symbol/convert_indentation/indent_lines/pwsh/write_csv |
| 14 | AUD-P1-02 | ②-3 截断 final 兜底排序 | gantry/agent_parse.py:560 | 加 `not outer_broken_tool` 守护并移到写入恢复之后 |
| 15 | AUD-P1-03 | ②-4 子代理非成功态当成功 | gantry/subagents.py:223-230、agent.py:871 | 只认显式成功态；发送失败出口补 `state="failed"` |
| 16 | AUD-P1-04 | ②-9 H3 残留 env/脱敏 | gantry/task_verify.py:132,266-275,340-343 | 验收子进程最小 env 白名单；stdout/stderr 同样 `_redact_secrets` |
| 17 | AUD-P1-05 | ④-4 H4 子代理 store | gantry/subagents.py:207-209 | final_gate 传 `b.TOOLS_EXTRA._task_store()` |
| 18 | AUD-P1-06 | ⑦-2 插件 name 越界 | gantry/plugins.py:580-581,649-671 | name 正则校验 + `resolve().relative_to(plugin_root)`，install/uninstall 双路径 |
| 19 | AUD-P1-07 | ⑧-2 定时导入模式残留 | webui/js/panels.js:307-322 | 合并按钮显式设 `dataset.mode='merge'`（打开选择器前设定） |
| 20 | AUD-P1-08 | ⑤-5 kb _root(ctx) TypeError | plugins/kb/plugin.py:67-68,158,386 | `_root()` 修掉错误调用；相对路径相对会话工作区解析 |
| 21 | AUD-P1-09 | ⑥-9 teams-bot _kb_reindex NameError | plugins/teams-bot/plugin.py:974 | 改为 `_kb(ctx).reindex(force=False)` |
| 22 | AUD-P1-10 | ③-2 导入 conversation_urls 绕过白名单 | gantry/routes_sessions.py:446-450 | 导入落库前/删除前统一过 `_copilot_delete_urls` |
| 23 | AUD-P1-11 | ③-3 运行中会话本地删除 | gantry/routes_sessions.py:451-464,494-504 | 仅本地删除也做 `session_running` 检查并 409 |
| 24 | AUD-P1-12 | ③-9 CSRF 白名单顺序 + 非 ASCII token | gantry/api.py:40-41,430-438,479-482 | 先判 allow_extra 再判 Sec-Fetch-Site；compare_digest 前 encode |
| 25 | AUD-P1-13 | ⑦-7 单实例锁 | start.py:373-375 | `data/.instance.lock` 独占创建（含端口），第二实例拒绝 |
| 26 | AUD-P1-14 | ⑦-6 更新锁进程级 | gantry/routes_update.py:15-17 | Updater 进程级单例/模块级 threading.Lock |
| 27 | AUD-P1-15 | 事件循环阻塞清单 | routes_providers.py:480、api.py:261、model_store 列表、atrefs.py、task_verify.py | OAuth 刷新 to_thread；巡检 light=True；模型库列表 to_thread；atrefs os.walk+to_thread；验收 to_thread |
| 28 | AUD-P1-16 | ⑨-4 依赖清单漂移 | pyproject.toml:21、requirements.txt | pyproject 补 pyyaml 等运行依赖、pypdf 约束对齐 lock；CI 加一致性检查 |

## P2（顺手修的低危项）

`⑧-3` token 弹窗循环（失败即停自动弹窗）、`⑥-4` OWA 发送验证（sent 前确认窗格关闭，未确认返回 unknown）、`⑥-5` open_teams 不劫共享页、`⑥-12` owa target.pop 空值、`③-8` 队列深度上限、`⑦-5` b.log 接入 logging、`⑦-9` lazy 模式 pending 归档、`⑨-6` 发布门 skip 白名单校验、`⑤-6` 超长段硬切、`⑤-10` _load_lock、`⑨-7` CHANGELOG 去重校验。其余低危/信息级按 TODO 跟踪，不强行本批完成。

## 完成定义

- P0 全部 12 项落地且 `python -m unittest discover -s tests`（或分块方式）全绿；
- 每项安全修复有对应回归用例；
- `scripts/cross_module_refs.py`、`node --check` 通过（涉及 WebUI 的项）；
- 全部改动集中在审核发现对应的最小文件集，无顺手重构。
