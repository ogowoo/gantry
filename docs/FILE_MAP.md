# 项目文件功能地图

> 按目录列出每个文件的功能，便于维护时快速定位。
> 自动提取模块 docstring/首行注释；核心文件用人工说明覆盖。
> 重新生成：`python scripts/gen_file_map.py`。

## 架构速览

```
start.py / app.py               启动与 FastAPI 装配
  └─ gantry/bridge.py           全局运行时（配置/浏览器/模型/工具）
       ├─ gantry/tools.py        工具注册表 + tool_plugins/
       ├─ gantry/agent.py        Agent 循环
       ├─ gantry/plugins.py      插件系统
       └─ gantry/routes_*.py     HTTP API
webui/                          Web 控制台（HTML/CSS/ES 模块）
plugins/                        插件（teams-bot 等）
tests/                          单元/集成/回归测试
scripts/                        运维、生成器、诊断工具
docs/                           架构/使用/开发文档
skills/                         Agent 技能说明与模板
```

## 维护入口

- **新增文件工具**：在 `gantry/tool_plugins/` 加 `<name>.py`，暴露 `register(registry)`；共享 helper 放 `_files_utils.py`。
- **新增 HTTP 路由**：在 `gantry/routes_*.py` 定义，确保 `gantry/api.py` 导入；复杂逻辑放 `gantry/<domain>.py`。
- **新增插件**：`plugins/<name>/plugin.json` + `plugin.py`，在 `setup(ctx)` 里注册工具/API/面板。
- **新增测试**：`tests/test_<area>.py`，用 `unittest`；跑全套 `python -m unittest discover -s tests`。
- **版本发布**：`python scripts/bump_version.py preview`（会改 version.py、pyproject、CHANGELOG、README）。
- **Teams 读/定位/发送**：读 `gantry/teams_idb.py`；定位 `gantry/teams_dom.py`（`open_chat_by_thread_id` / `open_chat_by_row`）；发送 `gantry/teams_send.py`；调度 `plugins/teams-bot/plugin.py`。

## 根目录

启动入口、项目元数据、依赖清单、顶层说明。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `.env.example` | 环境变量样例。 |  |
| `.gitattributes` | Git 属性（换行/语言统计等）。 |  |
| `.gitignore` | Git 忽略规则（data/、workspace/、.venv 等）。 |  |
| `AGENT.md` | Agent 使用/维护说明。 |  |
| `CHANGELOG.md` | 更新日志 |  |
| `README.md` | 项目主说明（英文）。 |  |
| `README.zh-CN.md` | 项目主说明（简体中文）。 |  |
| `README.zh-TW.md` | 项目主说明（繁体中文）。 |  |
| `app.py` | 应用装配层：创建 FastAPI、挂路由、生命周期与静态资源。 |  |
| `config.example.json` | 配置样例（提供商、浏览器、工作区等）。 |  |
| `install.py` | 安装脚本：依赖、浏览器、模型与可选组件。 |  |
| `pyproject.toml` | 项目元数据、依赖、脚本入口与工具配置。 |  |
| `requirements-embed.txt` | 向量/嵌入相关可选依赖。 |  |
| `requirements-local.txt` | 本地模型/推理相关可选依赖。 |  |
| `requirements-lock.txt` | 锁定版本的完整依赖清单。 |  |
| `requirements-ocr-xiaomi.txt` | 小米 OCR 相关可选依赖。 |  |
| `requirements-ocr.txt` | OCR 相关可选依赖。 |  |
| `requirements.txt` | 运行依赖清单。 |  |
| `start.py` | 统一启动入口：解析参数、选择模式、拉起 FastAPI/uvicorn。 |  |

## gantry/

核心 Python 运行时：API 路由、工具、Agent、浏览器自动化、模型、存储。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `__init__.py` | Gantry 核心包初始化。 |  |
| `agent.py` | Agent 主循环、工具调用与消息编排。 |  |
| `agent_parse.py` | Agent 输出解析：从模型文本提取工具调用/参数（ParseMixin）。 |  |
| `agent_prompt.py` | Agent 系统提示词与上下文拼装（PromptMixin）。 |  |
| `agent_text.py` | Agent 文本清洗、截断与格式化（TextMixin）。 |  |
| `agentrun.py` | agentrun.py — Agent 回合编排：等待回答、单次 chat、run_agent 主流程、回合标记与缓存、待办同步、纯对话回合 | wait_agent_answer, agent_chat_once, run_agent, _agent_chat_marker, stable_conversation_url, remember_initial_prompt |
| `answer.py` | 回答解析与界面锚点：从 Copilot 页面文本里找出"最新回答"的边界。 | _last_turn, extract_latest_answer, _status_before_marker, _has_complete_agent_action, copilot_unavailable_reason, license_reason |
| `api.py` | 对外 HTTP API 的聚合导出层（把各 routes_* 组合进来）。 |  |
| `atrefs.py` | `@路径` 引用展开（DSH 的 @文件 体验）：聊天里 @工作区里的文件，发消息时把内容带上。 | _resolve, expand_at_mentions, search_files |
| `auto_team.py` | 按 todo 自动派子代理（默认关闭）。 | is_enabled, _scope_of, plan_dispatch, build_tasks, maybe_dispatch |
| `brand.py` | 品牌与兼容层：Gantry（原 CopilotBridge Agent）。 | env, header_names, marker_prefixes, marker_prefix_regex |
| `bridge.py` | 全局运行时桥：配置、浏览器、模型、Agent、工具注册表的共享状态。 |  |
| `browser.py` | Playwright 浏览器生命周期、持久化 profile、登录态恢复。 |  |
| `cli.py` | 命令行入口（headless）：把服务当作本地 API 用，无需打开 WebUI。 | _client, _fail, _get, cmd_run, cmd_serve, cmd_fanout |
| `copilot_delete.py` | Copilot 侧栏会话删除流程（从 bridge.py 拆出）。 | __getattr__, _open_chat_sidebar, _conversation_link_by_title, _conversation_sidebar_link, _chat_row_from_link, _conversation_belongs_to_session |
| `copilot_tools.py` | Copilot 网页模型的 tool_calls 合成层。 | enabled, _tool_specs, _transcript, build_prompt, _repair_json, _loads_tolerant |
| `downloads.py` | downloads.py — Copilot 回答区下载：候选链接解析、href 落盘、调试快照、copilot_file（生成文件并下载进工作区） | _answer_area_html, _download_candidates, _resolve_href, pathlib_basename, debug_download_now, debug_page_snapshot |
| `editmatch.py` | 文本替换/补丁匹配（含模糊匹配）。 |  |
| `envfile.py` | `.env` 支持：密钥放 .env（不入库），config.json 里用 ${VAR} 引用。 | load_env_file, resolve_placeholders, warn_plaintext_secrets |
| `fanout.py` | 显式扇出：调用方直接给 N 个独立任务，各交给一个子代理。 | _normalize, _resolve_deps, _waves, _parse_tasks, _split_goal, _copy_workspace |
| `fileview.py` | 识别"给我看某个本地文件"的意图，并把文件内容回给界面渲染成卡片。 | _candidates, _resolve, _kind, maybe_file_view, render_message |
| `i18n.py` | 后端国际化。 |  |
| `interrupts.py` | 强行插入指令的"立即中断"信号（Ctrl+J）。 | _interrupt_event, request_interrupt, interrupt_pending, clear_interrupt, discard_interrupts, _await_interruptible |
| `invariants.py` | 运行时不变量注册表（借 DSH 的 dsh-invariants 思路，最小可用版）。 | webui_js_files, InvariantError, invariant, registered, check_all, _tool_groups_match_registry |
| `kb.py` | 知识库索引/搜索（SQLite FTS）。 |  |
| `kb_ingest.py` | 知识库文档导入：把 Word / PDF / Excel / PPT / HTML / 纯文本 / URL 变成 `data/kb/**/*.md`。 | IngestError, _decode, _html_to_text, _docx_text, _xlsx_text, _pptx_text |
| `kb_ocr.py` | OCR 兜底：扫描版 PDF / 图片 → 文本，用 RapidOCR（ONNX，CPU，纯 Python，中文强）。 | configure, config, available, available_xiaomi, effective_engine, _engine |
| `kb_semantic.py` | 知识库的向量检索层：把分块 embedding 进 SQLite，做语义召回，再与 FTS 结果融合。 | Embedder, HttpEmbedder, FastEmbedEmbedder, HashEmbedder, SemanticIndex, _cosine |
| `lifecycle.py` | 服务生命周期：让 HTTP 层能"优雅停机"（WebUI 的「终止服务/退出」按钮）。 | set_shutdown_hook, shutdown_pending, restart_requested, schedule, cancel_pending, set_force_exit |
| `local_models.py` | 内嵌本地模型引擎：把 GGUF 直接跑在 Gantry 进程里，`python start.py` 就能用，不需要外部 server。 | llama_class, set_llama_class, split_think, strip_think, _tag_prefix, _split_tag_prefix |
| `logcfg.py` | 日志落盘：托盘/后台模式下没有终端，日志必须写文件。 | log_dir, log_file, has_console, setup_logging |
| `memory.py` | 长期记忆存储与检索。 |  |
| `orchestration.py` | 编排层：ralph 目标迭代 / workflow 阶段编排 / 对抗式审计。 | run_ralph, run_workflow, _audit_parse, _audit_verdict_of, adversarial_audit |
| `owa.py` | Outlook Web 自动化后端。 |  |
| `page.py` | 通用页面操作/登录页识别/菜单选择。 |  |
| `plugins.py` | 插件系统：发现、加载、启停、面板、插件 API、权限。 |  |
| `power_automate.py` | Power Automate 工程/旧版包/解决方案包生成与校验。 |  |
| `power_automate_ui.py` | maker 门户 UI 自动化：probe/login/dom/import。 |  |
| `projection.py` | 事件投影（P2-10 第二期）：把事件流重放成状态，验证"日志足以重建状态"。 | project, compare_with_columns |
| `prompt.py` | 提示词分段注册表（借 DSH 的 SystemPrompt.section/order 思路）。 | PromptError, PromptRegistry, _cfg_lang, agent_protocol, agent_team, deliverable_rule |
| `prompt_lang.py` | 发给 Copilot 的提示词也要跟着语言走（简体 / 繁體 / English）。 | normalize, set_request_lang, request_lang, effective_lang, to_tw, localize |
| `provider.py` | API 提供商（OpenAI 兼容 HTTP）通道：协议约束、按会话历史、普通对话与 Agent 回合。 | _trim_provider_history, _repair_provider_history, discard_provider_agent_history, sync_provider_history_message, provider_turn, provider_session_chat |
| `providers.py` | 各 LLM 提供商的适配与发现。 |  |
| `queue.py` | 会话消息队列：跟进消息入队即返回，后台 worker 按序消费（DSH 式串行队列）。 | queue_depth, queue_items, _message_worker, cancel_message_queue, _process_queued_message, enqueue_session_message |
| `research.py` | research.py — 深研任务、插件任务与确定性扇出 | deep_research_task, run_plugin_task, fanout |
| `routes_agents.py` | Agent/子代理/记忆/自检相关路由。 |  |
| `routes_config.py` | 配置、插件、面板、插件 API 分发路由。 |  |
| `routes_core.py` | 核心路由：健康检查、重启、浏览器、Power Automate UI 等。 |  |
| `routes_sessions.py` | routes_sessions.py — 会话端点：列表/详情/快照/改动/回滚/消息/继续/插话/队列/事件/运行时/投影/审批/删除 | sessions, session_file, session_turns, session, session_snapshots, _session_workspace_root |
| `run_code.py` | 程序化工具调用（P2-13）：让模型写一段代码，在**独立进程**里批量调用只读工具。 | allowed_tools, run_code |
| `s2t_data.py` | 简→繁字符对照（**由 webui/i18n.js 的 S2T_CHARS 生成**,勿手改）。 |  |
| `s2t_extra.py` | 简繁补充表：`s2t_data.py`（由界面表生成）在**技术词**上有缺口。 |  |
| `session_store.py` | 会话/步骤持久化与检索。 |  |
| `snapshots.py` | 工作区快照：记录任务改动前的文件原像，支持整任务回滚（自我升级 failback）。 | SnapshotStore |
| `spans.py` | 本地 span 记录：把回合/工具调用写成 OTel GenAI 语义约定字段的 JSONL。 | _hex, SpanRecorder, Span, set_recorder, span |
| `subagents.py` | 子代理：新建 / 继续 / 分叉 / 列出（每个子代理是独立的会话与上下文）。 | subagent_depth, run_subagent, _drive_child, continue_subagent, fork_subagent, list_subagents |
| `teams.py` | Teams 自动化 facade：re-export DOM/发送/API/mentions 原语。 |  |
| `teams_api.py` | Teams chatsvc 内部 API 探测与可选发送。 |  |
| `teams_common.py` | Teams 公共常量、页面锁、配置、身份规范化。 |  |
| `teams_dom.py` | Teams 页面原语与 DOM 选择器（_SEL）：打开会话、点行、头部标题。 |  |
| `teams_idb.py` | 从 Teams IndexedDB 读会话/最后消息（读侧主路径）。 |  |
| `teams_logic.py` | Teams 纯逻辑：提及解析、问题提取、去重、分类（可离线测）。 |  |
| `teams_mentions.py` | Chat → Mentions 读取/点行打开会话。 |  |
| `teams_outbox.py` | Teams 发送 outbox：预留/成功/失败/幂等。 |  |
| `teams_send.py` | Teams 发送核心：定位会话、输入、点发送、标题校验。 |  |
| `todo_sync.py` | 把工作区里的 TODO 文件同步成会话的待办清单。 | _norm, find_todo_file, parse_todos, _clean_item, merge_todos, sync_todos_from_files |
| `tool_i18n.py` | 工具清单的多语言层：描述与参数提示按提示词语言输出。 | localize_items |
| `tool_plugin_loader.py` | 扫描 gantry/tool_plugins/*.py 并 register(registry)。 |  |
| `tool_plugins/_files_utils.py` | Shared helpers for file tool plugins. | guard_printable, hash_file, read_key, mark_read, require_read, diff_payload |
| `tool_plugins/append_text.py` | append_text tool plugin (auto-migrated from tools_files.py). | append_text, register |
| `tool_plugins/append_text_b64.py` | append_text_b64 tool plugin (auto-migrated from tools_files.py). | append_text_b64, register |
| `tool_plugins/apply_patch.py` | apply_patch tool plugin (auto-migrated from tools_files.py). | apply_patch, register |
| `tool_plugins/batch_analyze.py` | Structured read-only batch file analysis plugin. | register |
| `tool_plugins/capability_probe.py` | Read-only command capability probe plugin. | command_capabilities, register |
| `tool_plugins/convert_indentation.py` | convert_indentation tool plugin (auto-migrated from tools_files.py). | convert_indentation, register |
| `tool_plugins/copy_path.py` | copy_path tool plugin (auto-migrated from tools_files.py). | copy_path, register |
| `tool_plugins/delete_path.py` | delete_path tool plugin (auto-migrated from tools_files.py). | delete_path, register |
| `tool_plugins/find_files.py` | find_files tool plugin (auto-migrated from tools_files.py). | find_files, register |
| `tool_plugins/indent_lines.py` | indent_lines tool plugin (auto-migrated from tools_files.py). | indent_lines, register |
| `tool_plugins/init.py` | Built-in read-only coding harness tool plugins. | register |
| `tool_plugins/list_directory.py` | list_directory tool plugin (auto-migrated from tools_files.py). | list_directory, register |
| `tool_plugins/list_directory_tree.py` | list_directory_tree tool plugin (auto-migrated from tools_files.py). | list_directory_tree, register |
| `tool_plugins/list_subagent_models.py` | list_subagent_models tool plugin: discover routes usable by subagents. | list_subagent_models, register |
| `tool_plugins/make_directory.py` | make_directory tool plugin (auto-migrated from tools_files.py). | make_directory, register |
| `tool_plugins/move_path.py` | move_path tool plugin (auto-migrated from tools_files.py). | move_path, register |
| `tool_plugins/notebook_edit.py` | notebook_edit tool plugin: edit Jupyter notebook cells (Claude Code NotebookEdit). | _source_lines, _new_cell, notebook_edit, register |
| `tool_plugins/notebook_read.py` | notebook_read tool plugin: read Jupyter notebook cells (Claude Code NotebookRead). | _cell_source, notebook_read, register |
| `tool_plugins/power_automate.py` | Power Automate 工程生成工具（同租户、不调用真实 API）。 | power_automate_catalog, _parse_spec, _snapper, power_automate_generate, power_automate_validate, register |
| `tool_plugins/python_symbols.py` | python_symbols tool plugin (auto-migrated from tools_files.py). | python_symbols, register |
| `tool_plugins/read_text.py` | read_text tool plugin (auto-migrated from tools_files.py). | read_text, register |
| `tool_plugins/replace_lines.py` | replace_lines tool plugin (auto-migrated from tools_files.py). | replace_lines, register |
| `tool_plugins/replace_python_symbol.py` | replace_python_symbol tool plugin (auto-migrated from tools_files.py). | replace_python_symbol, register |
| `tool_plugins/replace_text.py` | replace_text tool plugin (auto-migrated from tools_files.py). | replace_text, register |
| `tool_plugins/session_changes.py` | session_changes tool plugin: list files changed in a session. | session_changes, register |
| `tool_plugins/session_read.py` | session_read tool plugin: read a prior/current session's conversation and steps. | _clip, session_read, register |
| `tool_plugins/session_search.py` | session_search tool plugin: search prior session history. | session_search, register |
| `tool_plugins/subagent_interrupt.py` | subagent_interrupt tool plugin: ask a running subagent to stop. | subagent_interrupt, register |
| `tool_plugins/todo_read.py` | todo_read tool plugin: read the current task list (Claude Code TodoRead). | todo_read, register |
| `tool_plugins/workspace_inspect.py` | Read-only workspace inspection plugin. | register |
| `tool_plugins/write_text.py` | write_text tool plugin (auto-migrated from tools_files.py). | write_text, register |
| `tool_plugins/write_text_b64.py` | write_text_b64 tool plugin (auto-migrated from tools_files.py). | write_text_b64, register |
| `tools.py` | 工具注册表与内置非文件工具；加载 tool_plugins。 |  |
| `tools_exec.py` | tools_exec.py — 代码执行与校验工具（ToolRegistry 的 mixin）：validate_python / run_unittest / run_code（程序化批量调用只读工具）/ run_python。 | ExecMixin |
| `tools_extra.py` | 扩展工具集（解耦模块，便于独立升级/更新/移除）。 | ExtraToolError, similar_task_hint, _Extra, register_extra_tools |
| `tools_files.py` | 文件工具兼容层：把旧 FilesMixin 方法转发到 tool_plugins。 |  |
| `tools_memory.py` | tools_memory.py — 长期记忆工具（ToolRegistry 的 mixin）：写入/检索/遗忘跨会话事实与偏好。 | MemoryMixin |
| `tools_office.py` | tools_office.py — Office 文档工具（ToolRegistry 的 mixin）：Excel / Word / PowerPoint / PDF 读写。 | OfficeMixin |
| `tools_outlook.py` | tools_outlook.py — Outlook 工具（ToolRegistry 的 mixin）：未读/阅读/搜索/发送，COM（经典 Outlook）与 OWA 网页后端两条路径。 | OutlookMixin |
| `tools_self.py` | tools_self.py — 自升级工具（ToolRegistry 的 mixin）：读写/替换服务自身代码、全量校验、自身单测与回滚（默认关闭，需 allow_self_upgrade）。 | SelfUpgradeMixin |
| `tools_skills.py` | tools_skills.py — 技能库工具（ToolRegistry 的 mixin）：列出/读取/新建目录式技能（SKILL.md）。 | SkillsMixin |
| `tray.py` | 系统托盘/后台进程管理。 |  |
| `ui_refs.py` | 前端 id 引用完整性：JS 里 `getElementById('x')`/`querySelector('#x')` 引用的 | check_ui_id_refs |
| `ui_src.py` | 前端源码取用（测试用）：`webui/*.js`（经典脚本）+ `webui/js/*.js`（ES 模块 + globals）。 | ui_js, ui_css, css_rule, fn_body |
| `version.py` | 版本号唯一来源与版本解析/比较工具。 |  |
| `window_pool.py` | Copilot 自动化窗口池：一个窗口 = 一个独立的 Copilot 网页标签页。 | WindowBusy, _page_closed, Window, WindowPool |
| `workspace_files.py` | workspace_files.py — 工作区文件与目录操作：写文件、建目录、系统目录选择对话框、目录浏览 | save_workspace_file, create_directory, pick_directory_native, browse_directories |

## plugins/

插件包；每个插件通常含 `plugin.json`（元数据/权限/面板）与 `plugin.py`（setup）。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `coding-harness-tools/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `coding-harness-tools/plugin.py` | Coding harness 工具插件：注册编码/校验相关工具。 |  |
| `deepseek-web/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `deepseek-web/plugin.py` | deepseek-web 插件：把 DeepSeek 网页版的反代接成 CBP 的一个提供商。 | normalize_models, probe, probe_sync, _refresh_loop, _start_refresher, _cfg |
| `image-gen/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `image-gen/plugin.py` | image-gen 插件：文生图（Grok / ChatGPT 那类"画一张"能力）。 | _env, _pick_backend, _dashscope, _openai, generate, setup |
| `kb/README.md` | kb（本地知识库） |  |
| `kb/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `kb/plugin.py` | kb 插件：把"本地知识库"做成可插拔能力（原来是塞在 teams-bot 里的两个工具）。 | _root, _cfg, _kb, _emb_cfg, _ocr_settings, _sem |
| `mcp/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `mcp/plugin.py` | MCP 客户端插件（stdio + streamable HTTP/SSE 两种传输，含 resources）。 | McpError, _safe, _schema_to_args, _content_text, _resource_text, McpStdioClient |
| `research/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `research/plugin.py` | research 插件：深度检索（把"搜 → 抓 → 归结"的机械部分做扎实）。 | _clean, _decode_ddg, _search_one, _fetch_excerpt, deep_research, setup |
| `schedule/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `schedule/plugin.py` | schedule 插件：定时任务（"每天 9 点汇总未读邮件" 这类 bot 能力）。 | _parse_every, _next_run, _fmt, Scheduler, setup, teardown |
| `teams-bot/README.md` | teams-bot |  |
| `teams-bot/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `teams-bot/plugin.py` | teams-bot 插件：Teams 网页版里"有人 @我"就回，**知识库优先**。 | _cfg, _kb, _sem, _search, _root, Bot |
| `telegram/README.md` | telegram 插件 |  |
| `telegram/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `telegram/plugin.py` | telegram 插件：用 Telegram 机器人操作 Gantry（手机端下任务、看进度、收回答）。 | _cfg, _env, _token, _allowed, _chats, _sid_for |
| `web-search/cordis.patch.yml` | YAML 配置/工作流。 |  |
| `web-search/plugin.json` | 插件元数据：权限、配置、面板、贡献点。 |  |
| `web-search/plugin.py` | web-search 插件：联网搜索（可插拔后端）。 | _env, _decode_ddg, _clean, _search_ddg, _search_gnews, _search_brave |

## webui/

Web 控制台；`index.html` + `css/` + `js/`（ES 模块）。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `boot.js` | 一次性迁移：旧 CBP 本地键迁移到 Gantry 键。 |  |
| `css/alerts.css` | 样式表。 |  |
| `css/changes.css` | 样式表。 |  |
| `css/chat.css` | 样式表。 |  |
| `css/composer.css` | 样式表。 |  |
| `css/dialogs.css` | 样式表。 |  |
| `css/layout.css` | 样式表。 |  |
| `css/overlays.css` | 样式表。 |  |
| `css/rows.css` | 样式表。 |  |
| `css/settings.css` | 样式表。 |  |
| `css/shell.css` | 样式表。 |  |
| `css/themes.css` | 样式表。 |  |
| `css/tokens.css` | 样式表。 |  |
| `favicon.ico` | 静态图片资源。 |  |
| `i18n.js` | 前端国际化：简/繁/英文案与简繁转换。 |  |
| `img/quickstart-kb.png` | 静态图片资源。 |  |
| `img/quickstart-main.png` | 静态图片资源。 |  |
| `index.html` | 页面模板。 |  |
| `js/animprefs.js` | 动画参数：示波器与输入框呼吸。 |  |
| `js/apistate.js` | API 状态与健康轮询（/health、模型/浏览器状态）。 |  |
| `js/approvals.js` | 破坏性工具审批 UI。 |  |
| `js/atmenu.js` | @ 文件引用菜单。 |  |
| `js/cards.js` | 消息卡片/变更卡片渲染。 |  |
| `js/codeux.js` | 代码块展示、复制与折叠。 |  |
| `js/composer.js` | 输入框、发送、附件与快捷操作。 |  |
| `js/control.js` | 运行控制：停止/继续/插话。 |  |
| `js/dialogs.js` | 通用对话框。 |  |
| `js/find.js` | 页内查找。 |  |
| `js/followload.js` | 跟随滚动与增量加载。 |  |
| `js/globals.js` | 跨模块共享的全局状态/函数。 |  |
| `js/kb.js` | 知识库管理：导入/列表/删除/检索。 |  |
| `js/main.js` | ES 模块入口与老代码兼容绑定。 |  |
| `js/manual.js` | 使用说明/帮助面板。 |  |
| `js/models.js` | 本地模型管理：列出/删除/下载 GGUF。 |  |
| `js/msgrender.js` | 聊天消息渲染。 |  |
| `js/panels.js` | 侧栏/面板与插件面板。 |  |
| `js/scope.js` | 任务进行中的示波器浮层。 |  |
| `js/sessions.js` | 会话列表与切换。 |  |
| `js/settings.js` | 设置页读写。 |  |
| `js/slash.js` | 斜杠命令。 |  |
| `js/state.js` | ES 模块共享可变状态。 |  |
| `js/steps.js` | 步骤/时间线渲染。 |  |
| `js/subagents.js` | 子代理面板。 |  |
| `js/teamcard.js` | 团队任务卡片。 |  |
| `js/themeboot.js` | 主题初始化（避免首屏闪烁）。 |  |
| `js/toast.js` | 轻提示/通知。 |  |
| `js/toollog.js` | 工具调用日志。 |  |
| `js/version.js` | 页签标题与版本号。 |  |
| `js/workspace.js` | 工作区文件浏览。 |  |

## tests/

单元/集成/回归测试；运行：`python -m unittest discover -s tests`。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `__init__.py` | 测试包初始化。 |  |
| `evals/tasks.json` | 数据/配置/示例 JSON。 |  |
| `fixtures/mock_mcp_http.py` | 最小 MCP **HTTP** server（streamable HTTP），用于测试 HTTP 传输与 resources。 | _rpc, Handler, MockMcpHttp |
| `fixtures/mock_mcp_server.py` | 最小 MCP server（stdio），只用于测试 MCP 客户端插件。 | _send, main |
| `test_adversarial_audit.py` | 对抗式审计（adversarial audit）：审计员并行 + 裁判核验 + 收敛判定。 | run, ParseTests, AuditFlowTests, AuditApiTests |
| `test_agent_parse.py` | AgentRunner 动作解析的回归测试。 | ParseTests, EscapeDecodeTests, TruncatedWriteRecoveryTests, WorkspaceContextTests, InstructionPathTests, TruncatedFinalTests |
| `test_anim_settings.py` | 「设置 → 外观 → 动画」：示波器 4 参数 + 输入框呼吸 3 参数。 | ControlTests, SaveToastTests, PrefsModuleTests, ScopeTests, CssTests, BootTests |
| `test_answer_noise.py` | 答案噪声：CBP 标记 / 无障碍提示 / 自己的问题回声都不能当成回答。 | CleanAnswerTests, SuggestionTailTests |
| `test_api_hardening.py` | 后端加固：跨站写入拦截、安全响应头、文档鉴权、日志脱敏、runtime 快照一致性。 | OriginGuardTests, SecurityHeaderTests, DocsAuthTests, RedactFilterTests, RuntimeSnapshotTests |
| `test_approval.py` | 破坏性工具的一次性审批：fail-closed、allowed-once、精确到参数。 | ApprovalStoreTests, ApprovalGateTests |
| `test_approval_scope.py` | 审批存储：allowed-once（逐参数）与本会话级授权（approval_scope=session）。 | ApprovalScopeTests |
| `test_approval_visibility.py` | 授权之后的可见性：用户已授权，时间线不该还停在"未获一次性授权"。 | ResumeHintTests, ApprovalVisibilityTests, ApprovedToolActuallyRunsTests |
| `test_atrefs.py` | @ 文件引用的回归测试（DSH 的 @ 体验）。 | AtRefTests |
| `test_audit_optimizations.py` | 审计优化回归：① 只读重复→回放缓存 ② 写操作重复仍被拦 ③ transient 标记 ④ 解析失败提示可操作。 | make_ws, CountingTools, run_loop, ReadonlyRepeatTests, SourceGuards, WorkLedgerTests |
| `test_audit_section6.py` | 审计第 6 节落地：回合预算提示 / 目录增量列出 / grep 定位规则 / 子代理复用提示。 | TurnBudgetTests, DirIncrementalTests, DirTreeTests, GrepFirstPromptTests, SubagentReuseTests |
| `test_auto_team.py` | `gantry/auto_team.py`：按 todo 自动派子代理的**决策**部分（默认关闭）。 | T, PlanTests, TaskTextTests |
| `test_backend_i18n.py` | 后端错误文案本地化：翻译表、模式匹配、未知原文回落、HTTP 头生效。 | TranslateTests, HandlerTests |
| `test_batch_actions.py` | 批量动作协议测试：一次回答执行多个工具动作（省回合）。 | act, BatchParseTests, TruncatedBatchRecoveryTests, BatchExecutionTests |
| `test_browser_boot_aborted.py` | 回归测试：首次启动的 `goto` 必须容忍 ERR_ABORTED。 | ErrAbortedTests |
| `test_changes_card.py` | 任务完成后的「改了哪些文件」卡片：快照原像 vs 当前文件的行级统计。 | ChangesStatsTests, DiffTests, ApiTests, FrontendCardTests |
| `test_chat_header_compact.py` | 对话面板头部压缩：4 行 → 3 行（次要按钮收进 ⋯ 菜单、说明改 tooltip）。 | ChatHeaderCompactionTests |
| `test_chat_windows.py` | 普通对话通道 / 管理操作的「窗口独占」语义回归。 | FakeWindow, _req, PlainChatWindowTests, BusySemanticsTests |
| `test_cleanup_marked.py` | cleanup-marked 端点的输入校验测试（用 TestClient，浏览器未起时也能跑 400/409 分支）。 | CleanupMarkedTests |
| `test_cli.py` | CLI（headless）：参数解析、对假服务的只读命令、连不上时的明确报错。 | Stub, CliTests |
| `test_compact_action.py` | 紧凑动作格式（无 type）+ Copilot 建议尾巴：解析器必须认。 | CompactActionTests |
| `test_composer_pinned.py` | 主列结构对齐 DSH：输入框是主列的最后一个 flex 子项，页面永不滚动。 | DshStructureTests, ComposerTabVisibilityTests |
| `test_copilot_file.py` | copilot_file：让 Copilot 生成文件并下载到工作区的回归测试。 | make_page, CopilotFileTests |
| `test_copilot_tool_shim.py` | Copilot 网页模型的 tool_calls 合成层（提示词 + JSON 解析）。 | ParseTests, PromptTests, DecideTests, RouteTests |
| `test_copilot_windows_setting.py` | Copilot 窗口数设置：schema、动态生效、运行时统计、子代理并发。 | WindowsSettingTests, WindowIsolationTests |
| `test_css_split.py` | 样式拆分（`webui/style.css` → `webui/css/*.css`）的结构回归。 | CssSplitTests |
| `test_deep_research.py` | 深研一键任务的错误路径（不打网络、不启浏览器）。 | DeepResearchErrorTests |
| `test_deepseek_web_plugin.py` | deepseek-web 插件自测：能否被真实 PluginHost 加载、注册提供商、面板与接口可用。 | _FakeProxy, PluginLoadTests |
| `test_delegation_targets.py` | 任务可拆性识别：给模型"可考虑扇出"的提示，不能对普通任务噪声化。 | DelegationTargetTests |
| `test_delete_requires_browser.py` | 远端会话删除：浏览器未就绪时要给出清晰错误，而不是 NoneType.goto 崩溃。 | EnsurePageTests, _false, _true |
| `test_deletion_plan.py` | 单测：删除计划能自动判断"这个任务用过 Copilot 吗"。 | DeletionPlanTests |
| `test_deliverable_wording.py` | 交付物措辞：产出文件就在本地工作区，回答里不能说"可下载"。 | DeliverableRuleTests |
| `test_directory_picker.py` | 工作目录选择器：目录浏览（对应 DSH 的 browse 后端）。 | BrowseTests, MkdirTests |
| `test_edit_tools.py` | 空白容错编辑：多级匹配、缩进校正、补丁原子应用、失败诊断。 | MatchTests, ReindentTests, PatchParseTests, RegistryEditTests |
| `test_envfile.py` | `.env` 支持的回归测试（用户要的：保持读系统环境变量的功能）。 | EnvFileTests |
| `test_error_taxonomy.py` | 限流与发送失败的分类：只有真正的服务侧限流才启动固定窗口与前端倒计时。 | ErrorTaxonomyTests |
| `test_esm_migration.py` | app.js → ES 模块（ESM 解耦）的**终态**回归测试。 | RetirementTests, RouteTests, NoBareSharedStateTests, TopLevelWindowCallTests |
| `test_event_log.py` | 事件日志（P2-10 第一期）：检查点动作会并行落事件；读取路径不变。 | EventLogTests |
| `test_event_source_switch.py` | P2-10 第三期开关：同一条事实两条读取路径（列/投影），不一致时回退到列。 | EventSourceTests |
| `test_fanout.py` | 确定性扇出：任务归一化、父会话消息、单项失败不影响其余、上限。 | FanoutTests |
| `test_file_ops_rule.py` | 文件操作必须走 Python 工具，不许拿 PowerShell 改文件。 | FileToolsArePurePythonTests, FileOpsRuleInjectionTests |
| `test_fileview.py` | 测试：文件直读（文本/图片/二进制 + 路径解析 + 不误判）。 | FileViewTests, ReferentialViewTests, NoopGuardScopeTests, MultiLangIntentTests |
| `test_first_turn_copilot_file.py` | 新话题首轮：Copilot 通道提示"可直接让 Copilot 生成文件"。 | _register_copilot_file, FirstTurnCopilotHintTests, OneShotProtocolTests |
| `test_force_delete_retry.py` | 回归：删除弹窗的「重试远程并强制清理本地记录」不能没反应。 | ForceDeleteRetryTests |
| `test_force_interrupt.py` | 强行插入的"立即中断"：提供商真取消、网页版半中断。 | InterruptPlumbingTests, AgentHandlesInterruptTests, ProviderHistoryOnInterruptTests |
| `test_gate_auto_validate.py` | 验证门自动补跑：模型忘记工具名时，系统代跑真验证，别让长任务白干。 | _b64, _ScriptedProvider, GateAutoValidateTests |
| `test_gate_proportional.py` | 验证门的比例原则：默认要求单测，任务声明"只做语法检查"时放宽。 | w, GateProportionalTests |
| `test_goal_continue.py` | 目标续轮（create_goal 的循环契约）。 | GoalContinueTests |
| `test_goal_persist.py` | 目标 / 计划在会话里的持久化（goal 模式曾因"列不存在"静默丢数据）。 | GoalPersistTests |
| `test_health_script.py` | 巡检脚本：区分"遗留会话（0 事件）"与"真不一致"，退出码可接 CI。 | HealthScriptTests |
| `test_image_attach.py` | 贴图（截图附件）链路的三处修复。 | CacheKeyTests, ProviderDropTests, UploadWaitTests, AttachStepVisibilityTests |
| `test_image_attachment.py` | 粘贴图片附件：后端 data URL 解码落盘与上限。 | data_url, ImageAttachmentTests |
| `test_import_side_effects.py` | 回归护栏：import 模块不得改动会话状态（今天最大的事故）。 | ImportSideEffectTests |
| `test_indentation_tools.py` | 缩进/空白编辑不再靠"字符串携带空格"——修掉空转死循环。 | IndentationToolsTests, ReplaceAllTests, RevealWhitespaceTests, AnswerUnfoldTests, RepeatGuardWiringTests |
| `test_initial_prompt.py` | 会话初始提示词：落库、只记第一次、旧会话近似重建、端点可查。 | InitialPromptTests |
| `test_inject_message.py` | 强行插入指令（DSH Ctrl+J 式）：运行中即时生效，不等当前这轮跑完。 | InjectEndpointTests, _act, InjectedPendingReachesNextTurnTests |
| `test_instruction_budget.py` | 项目指令预算：超预算丢宽泛优先、可见提示、闭合标签转义。 | InstructionBudgetTests |
| `test_interrupt_prominence.py` | 中断类提醒：① 文案必须完整翻译；② 必须显眼到不会被漏掉。 | InterruptI18nTests, InterruptProminenceTests |
| `test_invariants.py` | 不变量检查必须全通过；并验证它真的能抓到破坏（避免"永远绿"的假保险）。 | InvariantTests |
| `test_kb.py` | 本地知识库测试：切块、front-matter、中文检索（trigram）、增量重建、降级路径。 | ChunkTests, SearchTests, ScoreTests, ForceReindexTests |
| `test_kb_ingest.py` | kb_ingest 测试：各格式提取、URL 抓取（假网络）、落盘与 front-matter、插件导入/删除/收件箱。 | make_docx, make_xlsx, make_pptx, make_pdf, ExtractTests, SaveTests |
| `test_kb_plugin.py` | kb 插件的离线测试：清单、注册、检索/重建工具、HTTP 接口、引用格式。 | FakeCtx, _Req, KbPluginTests |
| `test_kb_semantic.py` | 向量检索层测试（全部离线：HashEmbedder，不依赖 18085 上的真模型）。 | HashEmbedderTests, EmbedderChoiceTests, SemanticIndexTests, PluginModeTests, VectorPathParityTests |
| `test_license_reason.py` | Copilot 许可证提示识别：不能当限流重试，也不能当正常回答。 | LicenseReasonTests |
| `test_line_editing.py` | 按行读 / 按行替换：把"模型被迫用 PowerShell 改文件"的推力去掉。 | LineReadTests, ReplaceLinesTests, NoShellForLinesRuleTests |
| `test_local_models.py` | 进程内模型引擎测试（全部用假 Llama，不加载真实模型，离线、秒级）。 | FakeLlama, Base, ThinkTests, EngineTests, EmbedderTests, ProviderTests |
| `test_locale_ja.py` | 界面语言（日文）下的锚点识别。 | JapaneseMarkersTests, JapaneseAnswerExtractionTests, JapanesePatternTests, ChatSurfaceTests |
| `test_locale_tw.py` | 界面语言（繁体中文）下的锚点识别。 | TraditionalChineseMarkersTests, TraditionalChineseAnswerExtractionTests |
| `test_long_session_render.py` | 超长会话打开也要快（用户："会话过长的时候连打开 session 都卡"）。 | LazyTimelineTests, LazyToolLogTests, DetailFetchOnceTests, LayoutCssTests |
| `test_mcp_plugin.py` | MCP 客户端插件：stdio 与 HTTP 两种传输、resources、错误与隔离。 | load_plugin, McpStdioTests, FakeCtx, McpReconcileTests, McpHttpTests |
| `test_memory.py` | 记忆层接线 + 语义测试。 | MemoryStoreTests |
| `test_memory_nudge.py` | 记忆提醒（memory_nudge）的回归测试。 | MemoryNudgeTests |
| `test_message_attachments.py` | 消息附件：粘贴的图片落盘后要能在聊天区回看。 | MessageAttachmentStoreTests |
| `test_message_dedupe.py` | 测试：同一条 assistant 回答不会被写成两条（队列 worker + run_agent 各写一次的旧事故）。 | DedupeTests |
| `test_message_edit_search.py` | 消息编辑/删除 + 跨会话搜索。 | MessageEditTests, CrossSessionSearchTests |
| `test_message_queue.py` | 跟进消息队列：入队即返回、按序消费、单条失败不阻断。 | make_stub, drain_until_empty, MessageQueueTests, QueueEditDeleteTests |
| `test_model_label_match.py` | 模型标签容错匹配：菜单标签与 ui_names 有细微差别时也要认出来。 | NormalizeTests, LabelMatchTests, RegistryTests |
| `test_model_menu_scan.py` | 模型菜单扫描回归测试：二级子菜单必须能被枚举到。 | ModelMenuScanTests, ModelAvailabilityTests |
| `test_new_task_isolation.py` | 护栏：新任务首轮必须显式声明"忽略此前对话话题"，且 new_chat 要验证对话为空。 | NewTaskIsolationTests |
| `test_no_hardcoded_paths.py` | 源码里不许出现**别人机器上的绝对路径**。 | _files, NoForeignAbsolutePathTests |
| `test_no_undefined_names.py` | 静态审计：包内不能有未定义名（NameError 隐患）。 | _scope_names, _module_names, _scan, audit, UndefinedNameTests |
| `test_noop_guard.py` | no-op 完成守卫（扩展版）：任务要求动手、却始终没有产出就 final 必须被拦。 | _ScriptedProvider, NoopGuardTests |
| `test_notebook_tools.py` | DSH / Claude Code 参考工具：Jupyter notebook 与 todo 读取。 | _notebook, NotebookToolTests, TodoReadTests |
| `test_openai_tool_messages.py` | OpenAI 兼容性：DSH / Agent 客户端会把工具回合也发进来。 | MessageSchemaTests |
| `test_orphan_subagents.py` | 单测：父任务结束时取消仍在跑的子会话。 | CancelChildrenTests |
| `test_page_scroll.py` | 页面级滚动必须为 0：顶部横幅不能让整页可滚（否则顶部一行被切）。 | PageScrollTests |
| `test_parent_state_with_subagents.py` | 父任务显示 completed、子代理却还在跑 —— 状态必须如实反映。 | _setup, RecursiveCancelTests, RunningCountTests, ApiAndFrontendTests |
| `test_parse_error_source.py` | 解析失败文案要按**模型来源**区分（此前硬编码 Copilot，Kimi 会话里误导）。 | ParseErrorSourceTests |
| `test_pending_guarantee.py` | 回归：跟进消息一定会出现在下一回合提示词里。 | PendingGuaranteeTests, PendingWrittenOnFollowupTests |
| `test_plain_text_policy.py` | 明文写入策略：文档可明文，代码必须 b64 分块。 | PlainTextPolicyTests, PromptPolicyWordingTests |
| `test_plan_mode.py` | 计划模式的循环契约：提交方案即结束本轮，不会继续动手执行。 | PlanModeTests |
| `test_plugin_i18n.py` | 插件自带译文：清单解析、只合并已加载插件、接口可用。 | _write, PluginI18nTests |
| `test_plugin_install.py` | 插件安装/升级/卸载：版本比较、备份、zip-slip 防护。 | _mk_plugin, InstallTests |
| `test_plugins.py` | 插件系统（gantry/plugins.py）：清单解析、权限、加载/卸载、启停、路由分发。 | _write_plugin, PluginTests |
| `test_power_automate_tools.py` | Power Automate 工程生成：definition / 工程目录 / 旧版导入包 / 解决方案包 / 工具。 | _spec, DefinitionTests, ValidationTests, ProjectTests, LegacyPackageTests, SolutionPackageTests |
| `test_power_automate_ui.py` | Power Automate maker 门户探测：无浏览器时安全降级 + 路由存在。 | ProbeTests |
| `test_precise_edit_guard.py` | 精确编辑守卫：禁止静默整文件覆盖，编辑前必须先读。 | PreciseEditGuardTests |
| `test_press_fallback.py` | issue #1 的回归测试：句柄失效时 `_press_box` / `_type_prompt` 必须逐级兜底。 | _Boom, _KeyboardLike, _Page, _Ok, PressFallbackTests, SendUsesHelpersTests |
| `test_projection.py` | 事件投影（P2-10 第二期）：重放能重建状态，并与数据库列一致。 | ProjectionUnitTests, ProjectionAgainstRealRunTests |
| `test_prompt_language.py` | 发给 Copilot 的提示词要支持简体 / 繁體 / English。 | ResolveTests, LocalizeTests, TraditionalTests, RegistryCallableTests, ApiWiringTests, WholePromptTests |
| `test_prompt_registry.py` | 提示词分段注册表：顺序、作用域遮蔽、变量 fail-fast、兼容既有拼接（逐字不变）。 | RegistryTests, SystemPromptUsesRegistryTests |
| `test_prompt_stability.py` | 系统提示词必须逐字稳定（提供商 prompt cache 的前提），且不含每步易变成分。 | PromptStabilityTests |
| `test_provider_agent_history.py` | API 提供商 Agent 通道：必须带会话历史 + 必须写 runtime（供 WebUI 面板显示）。 | _FakeProvider, ProviderAgentTurnTests |
| `test_provider_history.py` | 提供商对话历史的配对/结构：system 首位、user/assistant 严格交替、裁剪后仍合法。 | roles, ProviderHistoryTests |
| `test_provider_max_tokens.py` | OpenAI 协议的两个缺口（配本地 Ollama 时暴露）： | _FakeResp, _FakeClient, _provider, _run_complete, MaxTokensPayloadTests, ReasoningOnlyTests |
| `test_provider_net_error.py` | 提供商"不可用"的两个真问题： | NetErrorTextTests, ProviderCountdownTests |
| `test_provider_stream.py` | 提供商流式回合：边收边写 answer_preview（界面"逐字"看到的来源）。 | FakeStreamProvider, StreamProviderTurnTests |
| `test_provider_stream_status.py` | 提供商流式路径的状态检查：错误响应不能抛 httpx 的"未读流"错误。 | _provider_with, StreamStatusTests |
| `test_provider_structured_output.py` | 提供商结构化输出：能力映射、response_format 生成、不支持时自动降级。 | mk, StructuredOutputTests |
| `test_provider_tool_calls.py` | 提供商 tool_calls 透传：DSH 等 Agent 客户端需要用工具。 | _FakeResp, _FakeClient, _provider, _with_resp, ToolCallsTests, ChatCompletionsRouteTests |
| `test_provider_write_path.py` | 端到端：提供商（API）通道的 Agent 写文件路径必须真的落盘。 | _ScriptedProvider, b64, ProviderWritePathTests |
| `test_pwsh_timeout.py` | 单测：pwsh 超时要杀**进程树**（旧实现只杀直接子进程，孙进程持管道导致永不返回）。 | PwshTimeoutTests |
| `test_question_bar.py` | Agent 中途提问：回答完不能还挂着，重复回答不能报错。 | BackendClearsQuestionTests, FrontendQuestionBarTests |
| `test_quickstart_doc.py` | 快速手册的防遗忘测试：文档里写的入口/URL 必须真实存在且一致。 | QuickstartDocTests |
| `test_rail_drawer.py` | 遮罩（.railscrim）默认必须全局隐藏，否则会把 .app 网格挤坏。 | _strip_media_queries, ScrimGlobalHideTests |
| `test_rate_limit_suspend.py` | 限流挂起（suspended）：只有 Copilot 网页模型才挂起；挂起窗口里可换提供商模型继续。 | SuspendTests, SuspendWiringTests |
| `test_readme_langs.py` | README 三语版的回归测试（issue #3）。 | ReadmeLangTests |
| `test_rename.py` | 改名（CBP → Gantry）回归测试。 | FrontendParityTests, LegacyMarkerTests, EnvCompatTests, ServerBrandTests, I18nCoverageTests |
| `test_replace_text_newline.py` | 真实时间线（task-mv7zkarh，493 步）暴露的两个缺陷： | ReplaceTextNewlineTests, NoopGuardAfterRolloverTests |
| `test_restart_paths.py` | 重启路径回归测试（用户："重启的功能会因为路径问题导致重启失败"）。 | EntryArgvTests, ExecRestartChdirTests |
| `test_restart_service.py` | 重启服务（WebUI 菜单）：优雅退出 → 就地 exec 重启。 | LifecycleRestartTests, ScheduleTests, ExecRestartTests, RestartApiTests, UiWiringTests, StartScriptRetryTests |
| `test_result_retention.py` | 工具结果保留策略：保头保尾（尾部常含错误与结论），而不是只留头部。 | RetentionTests |
| `test_resume_paused.py` | 暂停无法恢复的死锁回归。 | ResumePausedRunnerTests |
| `test_retry_count_persistence.py` | 限流重试计数必须跨"挂起→恢复"延续，不能每次重置为 1。 | RetryCountPersistenceTests |
| `test_retry_countdown.py` | 服务限流倒计时：会话字段持久化 + 锚定首次超限 + 固定窗口。 | RetryCountdownStateTests, RetryWindowAnchorTests |
| `test_retry_mask.py` | 限流提示：全页面遮罩（含换模型按钮）、不再撑大间距、英文完整。 | RetryMaskStructureTests, RetryI18nAndLogicTests, ServiceUnavailableBadgeTests |
| `test_run_code.py` | 程序化工具调用（P2-13）：子进程隔离 + 只读白名单 + 限额。 | RunCodeTests |
| `test_sandbox_errors.py` | 沙箱拒绝必须结构化且可行动（DSH 的 FS_SANDBOX_DENIED 思路）。 | SandboxErrorTests, SandboxPathHintTests, B64PayloadGuardTests |
| `test_sandbox_modes.py` | 沙箱三档 + 计划模式：变更类工具在只读档位/计划模式下必须被拦下。 | SandboxModeTests |
| `test_sandbox_picker.py` | 权限档位（仿 DSH）：read-only / workspace-write / danger-full-access。 | BackendContractTests, FrontendTests |
| `test_schedule_plugin.py` | schedule 插件：工作区参数必须真正传到 run_task（不能只存不用）。 | load_plugin, FakeCtx, ScheduleWorkspaceTests |
| `test_scopes_and_self_upgrade.py` | 全局/项目两级 AGENT.md 与 skills、以及自升级工具链的测试。 | build_runner, write_skill, TwoScopeTests, SelfUpgradeTests |
| `test_self_search.py` | 自升级搜索工具的测试：模糊定位现在能走通了。 | SelfSearchTests, ToolRegistrationTests |
| `test_self_snapshot_scope.py` | 自升级快照作用域命名 / 可列出的回归测试。 | SelfScopeNamingTests |
| `test_self_upgrade_path.py` | 测试：自升级规则里必须带真实安装目录，且明确"别按名字猜路径"。 | SelfUpgradePathTests |
| `test_self_upgrade_recovery.py` | 自升级任务 task-mvs4un2x 的失败复盘 → 回归测试。 | ActionParseRepairTests, SelfTextToolTests, SandboxHintTests, PendingMarkerGateTests |
| `test_self_upgrade_wiring.py` | 回归测试：自升级开关的接线（这次是真机发现的全链路问题）。 | SelfUpgradeWiringTests |
| `test_send_failure_pause.py` | 发送失败退避期间的暂停：CopilotSendFailed 的 backoff 循环必须检查 paused。 | SendFailurePauseTests |
| `test_send_guard.py` | 护栏：Copilot 未就绪时要明确失败，不能一直挂在 sending。 | SendGuardTests |
| `test_session_list_consistency.py` | 侧栏列表与会话页的数字必须来自同一口径。 | _build_store, TotalsAllTests, ListEndpointTests, FrontendConsistencyTests |
| `test_session_model.py` | 会话级模型：接口切换 + 续跑沿用（服务重启后不丢）。 | SessionModelTests |
| `test_session_revive.py` | 体验细节：cancelled 会话发消息应自动恢复；凭据失效的错误要能看懂。 | CancelledReviveTests, CredentialHintTests |
| `test_session_state_icon.py` | 会话行的状态用 SVG 图标表示，不再用文字块。 | SessionStateIconTests |
| `test_session_tag.py` | 会话标签：列表分组用的本地元数据（不参与 Agent 逻辑）。 | SessionTagTests |
| `test_session_tools.py` | DSH 参考工具：会话检索 / 会话读取 / 变更清单 / 子代理控制。 | SessionSearchStoreTests, SessionToolRegistrationTests, SessionChangesAggregationTests |
| `test_settings_config.py` | 设置页：配置白名单接口 + 前端入口/弹窗的单测。 | ConfigEndpointTests, SettingsDialogTests |
| `test_shutdown.py` | 终止服务 / 退出：UI 按钮背后要真的能停掉服务。 | LifecycleTests, ShutdownEndpointTests, tempfile_dir |
| `test_skill_create.py` | skill_create 工具测试：两级作用域、权限门、覆盖保护与 frontmatter 格式。 | SkillCreateTests |
| `test_skill_power_automate.py` | power-automate 技能测试：可发现、可读、模板 JSON 可解析、要点覆盖。 | PowerAutomateSkillTests |
| `test_slash_followup.py` | 「继续对话」输入框的斜杠菜单（`/`）回归。 | fn_body, FollowupWiringTests, SlashModuleTests |
| `test_snapshot_versions.py` | 快照版本化（按 Copilot 回合计版本）：一次 complete = 一个版本。 | SnapshotVersionTests |
| `test_snapshots.py` | 工作区快照与回滚的回归测试。 | SnapshotStoreTests, ToolSnapshotHookTests |
| `test_spans.py` | 本地 span：字段对齐 OTel GenAI 语义约定、并发安全、失败不影响主流程。 | SpanTests |
| `test_spans_report.py` | span 报表：百分位与聚合口径。 | rec, SpanReportTests |
| `test_spill.py` | 结果 spill 落盘：超大结果写工作区 .spill/，上下文只留摘要+路径提示。 | SpillTests |
| `test_sse_events.py` | SSE 实时推送：runtime 快照/队列/新消息直接推给浏览器，前端不必轮询。 | EventBusTests, EventsEndpointTests |
| `test_stale_answer.py` | 回归：追加指令后必须等**新一轮回答**，不能把上一次的最终答复当成新回答。 | StaleAnswerTests |
| `test_step_checkpoint_wiring.py` | 派发检查点接线：每次工具执行前后都会登记/收尾（结果是"崩在途中可识别"的前提）。 | CheckpointWiringTests |
| `test_step_dispatch.py` | 工具派发检查点：结果未知的副作用步骤可被识别。 | StepDispatchTests |
| `test_subagent_back_nav.py` | 从子代理会话必须能回到父会话，且子代理面板不能消失。 | SubagentBackNavTests |
| `test_subagent_continue.py` | 子代理：分叉 / 继续 / 列表（不跑真实模型，只验证会话层行为与错误路径）。 | SubagentForkTests |
| `test_subagent_depth.py` | 子代理深度自愈：中断残留的记录不能永久堵死子代理。 | DepthTests |
| `test_subagent_max_steps.py` | 子代理步数上限可配置（用户实测：默认 40 步在做完前就用尽）。 | SubagentMaxStepsTests |
| `test_subagent_parallel.py` | 子代理并行扇出（仅 API 提供商通道）。 | _setup, ParallelFanoutTests, SubagentManyToolTests, DriveChildConcurrentTests, TaskContextIsolationTests |
| `test_subagent_stats.py` | KPI 的步数/token 必须把子代理算进来。 | _mk_store, SubagentStatsTests, ApiExposureTests, FrontendKpiTests, StoreSourceTests |
| `test_suggestion_tail.py` | 补充测试：剥掉 Copilot 的建议尾巴。 | StripSuggestionTests |
| `test_sync_tools.py` | 同步工具经 execute() 调用的回归测试（skill_list 曾因无条件 await 永远失败）。 | SyncToolExecutionTests |
| `test_teams_bot.py` | teams-bot 插件测试（离线）：读侧走 IndexedDB（假数据），写侧默认关闭。 | dm, conv, FakeCtx, FakeKB, FakeTeams, BotTestCase |
| `test_teams_guard.py` | Teams 发送前身份核验与页面互斥锁（纯离线）。 | conv, IdentityTests, PageLockTests, TabReuseTests |
| `test_teams_logic.py` | Teams 自动化模块的"纯逻辑"测试（不碰浏览器）。 | MentionTests, MentionMetadataTests, ExtractTests, DedupeTests, KbAnswerTests, ShouldAnswerTests |
| `test_teams_mentions.py` | Mentions 读取的纯逻辑：badge 解析 + 行规范化。 | BadgeTests, RowTests |
| `test_teams_outbox.py` | 发送幂等箱：reserved/sent/failed 状态机与重启后的防重复行为。 | OutboxTests |
| `test_teams_send.py` | Teams 发送定位：标题校验容忍 "姓名 - 公司" 后缀。 | TitleMatchTests |
| `test_teams_thread_id.py` | open_chat_by_thread_id：按 data-fui-tree-item-value 精确点击的单测。 | FakeTitle, FakeRow, FakeMouse, FakePage, ThreadIdTests |
| `test_telegram_plugin.py` | telegram 插件的单元测试（不碰真 Telegram API，全部 mock）。 | FakeCtx, run, TokenAndGatingTests, SessionMappingTests, CommandTests, SetupTeardownTests |
| `test_thinking_placeholder.py` | 回答抽取：Copilot 的"思考中"占位文本不能当成答案。 | ThinkingPlaceholderTests, StatusBubbleTests |
| `test_timeline_export_order.py` | 导出时间线的数组顺序必须"自证"，不能让人猜。 | ExportOrderingTests, RealExportShapeTests |
| `test_timeline_fill.py` | 步骤时间线要铺满父卡片，不能被 `.livepane` 的高度上限卡住。 | _rule, TimelineFillsParentTests |
| `test_toast_placement.py` | 提示信息（toast）统一位置：顶部居中一列，不再散落在左下角。 | blocks, toast_rule, ToastPlacementTests |
| `test_todo_bar.py` | 待办清单（todo_write）：标签、计数口径、可展开。 | TodoBarTests, TodoWriteShapeTests |
| `test_todo_subagent_visibility.py` | 待办 / 子代理面板：新一轮会话没有时不能显示。 | ClearOnNewSessionTests, SyncTeamCardTests |
| `test_todo_sync.py` | `gantry/todo_sync.py`：把工作区 TODO 文件同步成会话待办。 | ParseTests, MergeTests, SyncTests |
| `test_tool_arg_errors.py` | 工具参数错误必须可行动：列出合法参数 + 高频坑提示。 | ToolArgErrorTests |
| `test_tool_labels.py` | 工具在界面里的显示名（read_text → 读取文本 / Read text），title 保留原始 id。 | ToolLabelTests |
| `test_tool_log_one_line.py` | 调用记录：默认一行（最新），点它**行内展开**——与待办/子代理/状态同一种方式。 | ToolLogInlineTests |
| `test_tool_visibility.py` | P0-1 工具按任务裁剪：core 常在、专业组按关键词展开、隐藏≠禁用。 | ToolVisibilityTests |
| `test_toolbar_compact.py` | 工具栏收敛成一行（用户选的方案 2）：主操作只留「恢复 / 取消任务」。 | _actions_block, _primary_buttons, ToolbarCompactTests, MainColumnRegressionTests |
| `test_tools_extra.py` | 扩展工具（gantry/tools_extra.py）：搜索 / 网页 / 命令 / 任务清单 / 图片 / 提问。 | ExtraToolsTests, ExtraIsolationTests, TeamToolsTests |
| `test_tray.py` | 系统托盘 + 后台启动（`python start.py` 派生 pythonw）的回归。 | IconTests, MenuTests, I18nTests, DetachTests, CrashLogTests, HangDumpTests |
| `test_turn_cache.py` | 回合结果表：同一提示词重放时复用答案、步数门槛、级联删除。 | TurnCacheTests |
| `test_two_queue.py` | 两级队列（P1-8）：next-turn 重排计划；next-step 立即生效并抢占批量剩余动作。 | StepQueueRunnerTests, StateKeyPersistenceTests |
| `test_ui_control_styles.py` | 控件样式回归：「强行插入」对话框的两个单选项被撑变形。 | InputStylingTests |
| `test_ui_id_refs.py` | 前端 id 引用完整性（ui_refs）：JS 里引用的元素必须在 index.html 或动态生成里存在。 | CheckFunctionTests, RealCodeTests |
| `test_unknown_outcome.py` | 结果未知封锁：超时后禁止原样重试不可幂等动作，直到只读动作核对过。 | UnknownOutcomeGateTests |
| `test_validation_gate.py` | 验证闭环旗的持久化回归测试。 | act, GatePersistenceTests |
| `test_version.py` | 版本相关的回归测试：保证"单一来源"不被破坏。 | VersionFormatTests, SingleSourceTests |
| `test_version_chip.py` | WebUI 版本信息与侧栏底栏（DSH 式：⚙ 设置 + 版本号固定在侧栏底部）。 | css_block, RailFooterTests, SettingsI18nTests |
| `test_view_cache.py` | 从子代理返回父会话不能"整棵重建"（用户："从 subagent 返回到 live stream 的时候会很卡"）。 | ViewCacheSourceTests |
| `test_web_search_plugin.py` | web-search 插件的解析与回退（离线：不联网，喂固定 HTML/XML）。 | DdgParseTests, GNewsParseTests, FallbackTests |
| `test_web_tool_provider_gate.py` | provider（API）会话禁用"需要 Copilot 网页会话"的工具。 | WebToolGateTests, ProviderSessionToolSurfaceTests |
| `test_window_no_close.py` | 窗口闪退的回归：release 不关页面、有任务时重建浏览器要跳过、失败不杀 Playwright。 | FakePage, ReleaseNeverClosesTests, NoRelaunchWhileBusyTests |
| `test_window_pool.py` | 窗口池单测（纯逻辑，不碰浏览器）。 | run, FakePage, PoolTests |
| `test_workspace_guard.py` | 工作区边界护栏：把服务安装目录当工作区必须显式开启自升级；敏感路径始终拒绝。 | WorkspaceGuardTests |
| `test_workspace_isolation.py` | 会话工作区必须真正生效（用户："工作区的选择好像不生效，看我截图"）。 | WindowIntegrationTests, CloneRebindTests, ExtraToolsActiveRegTests, SubagentRootTests, FrontendWorkspaceTests |

## scripts/

运维、生成器、诊断脚本；用 `.venv\Scripts\python.exe` 运行。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `audit_failures.py` | 审计第三步：把浪费最多的三类失败摘出原文—— | show |
| `audit_schema.py` | 审计第一步：摸清 agent.db 结构，取出 pyshot 任务（task-mvfc11u8）的全部执行数据。 |  |
| `audit_trace.py` | 审计第二步：从事件日志重建 pyshot 任务的执行轨迹，统计工具/状态/失败模式。 |  |
| `audit_turns.py` | 审计用：回合配对 / 延迟分布 / 会话切换点（报告第 8 节的数据来源）。 |  |
| `bump_version.py` | 版本管理脚本：改号 / 更新 CHANGELOG / 打标签 / 推送（跨机器协作的统一入口）。 | _write, set_version, roll_changelog, run, main |
| `calibrate.py` | conv_stats 阈值校准分析：用实测数据反推 compact/rollover 配置。 | _default_db, pct, main |
| `cross_module_refs.py` | 跨模块共享检查（ESM 迁移期护栏）。 | strip_noise, decls, toplevel_window_calls, analyze, main |
| `css_style_diff.py` | CSS 重排的**行为不变**证明：逐元素比对计算样式（computed style）。 | run, compare |
| `diag_composer.py` | 诊断：空状态（未选会话）下底部 followup 输入框为什么还显示。 |  |
| `diag_teams_dom.py` | 分析 Teams 页面 dump：找出真实的 data-tid / role 结构，用来校准选择器。 |  |
| `diag_teams_probe.py` | 跑 probe 并把 DOM 摘要打印出来（定位会话列表/消息/输入框的真实结构）。 |  |
| `diag_teams_probe2.py` | 完整打印 probe 结果（不重启服务，浏览器是热的）：tids 全量、列表容器、样本项、输入框、消息。 |  |
| `diag_ui.py` | 诊断探针：当前页面的会话列表/时间线/工具记录是否真的画出来了（含 console 错误）。 |  |
| `e2e_stop_at.py` | 端到端：@ 引用展开 + 发送按钮运行中变停止。 | call |
| `eval.py` | 轨迹级回归评测：把"Agent 该怎么做"固化成可重跑的断言。 | check_task, main |
| `gen_file_map.py` | 生成 docs/FILE_MAP.md：按目录列出每个文件的功能与关键入口。 | files, py_info, js_info, md_info, describe, main |
| `gen_s2t.py` | 从 webui/i18n.js 的 S2T_CHARS 生成 gantry/s2t_data.py（简→繁对照）。 | main |
| `health.py` | 会话健康巡检：事件日志 vs 数据库列的一致性（P2-10 第二期的运行时验收）。 | load_sessions, check_session, main |
| `i18n_coverage.py` | 多语言覆盖审计：在英文模式下把每个界面状态都过一遍，抓出仍然显示中文的文本。 | states, main |
| `i18n_static_scan.py` | 不变量：index.html 里的中文文本/属性都要有英文词条（含隐藏弹窗）。 | check |
| `invariants.py` | 跑一遍运行时不变量检查（提交前/CI 可用，退出码非 0 表示有契约被破坏）。 | main |
| `kb.py` | 本地知识库命令行：建索引 / 搜一下 / 看规模。 | _emb_cfg, _index, _search, main |
| `login.py` | 浏览器登录辅助：检查 Playwright/服务、重置 profile、执行登录。 |  |
| `login_teams.py` | Teams 登录辅助：打开登录页并等待人工完成 ADFS/MFA。 |  |
| `make_manual_shots.py` | 生成使用手册的截图（主界面 / 知识库管理页），放到 webui/img/。 |  |
| `make_quickstart_card.py` | 生成一页版「快速使用卡片」图片：简体 / 繁體 / English 各一张。 | esc, html, main |
| `make_tray_icon.py` | 应用图标（favicon + 托盘）用**字母 G**，并输出一张预览图便于核对。 | _font, draw, main |
| `outlook_selftest.py` | Outlook COM 自检：在跑服务的机器上、用服务同一个 Python 环境运行。 | ok, fail, explain, main |
| `power_automate_gen.py` | Power Automate 工程生成 CLI（同租户、不调用真实 API）。 | main |
| `power_automate_verify.py` | 生成 Power Automate 连接器 operationId 验证包（每个连接器一个最小流）。 | main |
| `probe_fflm.py` | 测 FastFlowLM（NPU 上的 OpenAI 兼容服务）：模型名、正文/思考字段、耗时。 | chat |
| `probe_fflm_json.py` | FastFlowLM 是否支持 response_format（Gantry 的 JSON 模式依赖它）+ 模型清单。 | call |
| `probe_ollama_protocol.py` | 看这个 4B 模型在"Agent 协议"下的原始输出长什么样（判断是模型问题还是解析问题）。 |  |
| `provider_selftest.py` | 提供商连通性自检：一条命令定位"浏览器能访问、服务说不可用"。 | mask, load_cfg, probe, main |
| `reap_browsers.py` | 清理"泄漏的 Playwright 浏览器"。 | _ps, list_browsers, main |
| `regression.py` | 整体回归：**真跑**每个面向用户的能力，而不是只跑单测。 | Ctx, step_health, step_tools, step_sandbox_api, step_plan_mode, step_write_task |
| `resume_and_check.py` | 重启后：恢复 pyshot 任务，并确认 todo 从文件同步进来了。 | get, post |
| `rollback.py` | 工作区改动回滚 CLI：服务/WebUI 起不来时的 failback 通道。 | project_root, main |
| `run_tests_chunked.py` | 分模块跑测试（每个模块独立进程 + 超时），用于排查"整体跑会卡住"的次序问题。 | main |
| `scan_chats.py` | 逐个打开会话，报出每个的参与人数/类型/首条消息作者，找出真正的 1:1。 | get, post |
| `selftest.py` | 新版本服务自检：只读端点 + 一次真实 Copilot 发送 + 文件直读，做完清理。 | call, check |
| `shot_tray.py` | 截屏右下角通知区，确认托盘图标真的出现了（只看截图，不依赖进程名）。 |  |
| `smoke_anim.py` | 浏览器验证：设置页「动画」分组的 8 个控件真的能改、改了什么真的生效。 |  |
| `smoke_anim_i18n.py` | 英文模式下的设置页「动画」分组：不许露中文（label + title 都查）。 |  |
| `smoke_composer.py` | 冒烟：任何状态下"可输入的输入框"只允许有一个。 |  |
| `smoke_favicon.py` | 验证浏览器里的 favicon 就是那个 G（与托盘同一份 webui/favicon.ico）。 |  |
| `smoke_interact.py` | ESM 解耦后的"真交互"冒烟：点开会话 → 看时间线/工具记录/待办是否真的画出来。 |  |
| `smoke_modules.py` | 浏览器冒烟：所有迁出模块的函数都绑到了 window、无 JS 错误。 |  |
| `smoke_ollama_ui.py` | 浏览器验证：WebUI 的模型下拉里能看到 ollama 模型，并能选它。 |  |
| `smoke_restart_action.py` | 端到端验证托盘的「重启服务」动作：真的重启 + 等 /health 回来。 | spy |
| `smoke_save_toast.py` | 复现用户场景：只调 TeamScope 参数 → 点「保存」→ 提示必须不是"没有改动"。 | last_toast |
| `smoke_scope.py` | 浏览器验证（续 2）：先打开一个会话（卡片才会真正布局），再量示波器参数效果。 |  |
| `smoke_slash.py` | 浏览器验证（含截图）：跟进框的 / 菜单能弹出、选择真的变了、Enter 只补全不发送。 | menu |
| `smoke_version.py` | 验证：版本号与 /health 一致（不写死某个预览号），且根路径是 302 → /ui/。 |  |
| `smoke_web_search.py` | 联网验证：重启后的服务里，web_search 对"今天 AI 新闻"这类 query 必须有结果。 |  |
| `spans.py` | span 分析：把 data/spans.jsonl 聚合成可读报表（零依赖）。 | pct, agg, summarize, analyse, print_report, main |
| `test_8b_news.py` | 复现用户那一问（8B + Agent 模式），看修好搜索工具后能不能搜到真东西。 | post |
| `test_click_switch.py` | 验证点击位置修复：按索引打开不同会话，看头部标题是否真的变化（只读，不发送）。 | get, post |
| `test_fflm.py` | 经 Gantry 测 FastFlowLM 的 DeepSeek-R1 8B：普通对话 + 一次小 Agent 任务。 | post |
| `test_ollama_agent.py` | 真跑一遍 Ollama 上的 Agent 任务，并把每一步动作/耗时打出来。 |  |
| `test_p1_live.py` | P1 端到端验证（只发 Chen, Alex 自己会话）：触发消息 → cycle 入队 → worker 真发送。 | post, get, main |
| `ui_contrast_audit.py` | 主题对比度审计：按 WCAG 实测 7 套主题下的关键文字对比度。 | main |
| `ui_i18n_audit.py` | i18n 覆盖率审计：三语言逐面切换，列出未翻译/漏 key/原始 key 泄漏。 | s2t_simplified_chars, en_sentence_values, audit, main |
| `ui_layout_audit.py` | UI 布局审计：在多种视口宽度下**实测**溢出、遮挡、固定元素碰撞，并截图存档。 | audit, main |
| `ui_smoke.py` | WebUI 冒烟测试：用真实浏览器加载 /ui，捕获控制台错误并验证关键行为。 | run, main |
| `verify_ctx_fit.py` | 复现并验证修复：向 local/minicpm5-2b 发一个约 1 万 token 的提示。 | post |
| `verify_full_reload.py` | 整套重载后的验证：版本 / 插件加载 / 工具注册 / IndexedDB 读侧 / 安全开关。 | get, post |
| `verify_kb_auto_live.py` | 线上验证 auto：① 普通文字截图应走 RapidOCR（快）；② 表格截图应自动转 Xiaomi（OTSL）。 | post, get, png |
| `verify_kb_import.py` | 线上验证：通过 kb 插件接口导入 docx / pdf / 文本 / URL，再检索、删除。 | post, get |
| `verify_kb_ocr.py` | 真机验证 OCR：造一张中文图片（PIL + 微软雅黑）→ RapidOCR 识别 → 时间/文本。 |  |
| `verify_kb_ocr_pdf.py` | 扫描版 PDF：用 fpdf 把图片嵌进 PDF（无文本层），验证 kb_ingest 自动回退 OCR。 |  |
| `verify_kb_page.py` | 验证知识库管理页：打开弹窗 → 出现统计/文档列表 → 检索有结果 → 关闭。 | run |
| `verify_kb_vector.py` | 验证 preview.50：kb 插件加载 + 向量工具/接口在线可用。 | get, post |
| `verify_kb_xiaomi_live.py` | 线上验证 preview.56：① kb_add 工具注册；② engine=xiaomi 的 OCR 导入；③ /kb 与 📚 前端可用。 | get, post |
| `verify_local_models.py` | 验证 preview.51：内嵌模型（local/minicpm5-2b）可用，且解耦模式（外部 HTTP）不受影响。 | get, post |
| `verify_lynn_chat.py` | 热重载后去 Lynn Ling 会话里看：能否滚动找到、是否是 1:1、作者归属对不对。 | get, post |
| `verify_manual.py` | 验证手册里的配图真的加载出来了（naturalWidth>0），并统计章节/图注。 |  |
| `verify_reload2.py` | 等新进程（preview.47）真正就绪后再验证整套：插件 / 工具 / IDB 读侧 / 安全开关。 | get |
| `verify_teams_bot.py` | 一次跑完：等版本更新 → 开 dry_run 跑一轮 → probe 看作者名 → 复位关闭。 | get, post |
| `verify_teams_e2e.py` | 端到端 dry_run 验证：给一个真实会话，把 mention_tokens 临时设成一个真实出现过的词， | post |
| `verify_xiaomi_ocr.py` | 实测 Xiaomi-OCR-0（0.8B, Q4_K_M + mmproj）在 llama_cpp 里能否跑图片 OCR。 | try_handler |
| `verify_xiaomi_ocr_hf.py` | 用官方组合（transformers 5.17 + torch 2.14 CPU）跑 Xiaomi-OCR-0，测中文文档图片。 |  |

## docs/

架构、使用、开发与排障文档。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `ANALYSIS.md` | Gantry 代码与工程分析报告 |  |
| `ARCHITECTURE.md` | Gantry 架构基线 |  |
| `CSS-SPLIT.md` | 样式结构：`webui/css/*.css`（12 个文件） |  |
| `DELIVERY-SUMMARY.md` | 交付物登记与遗留问题汇总 |  |
| `ESM-MIGRATION.md` | app.js → ES Modules 迁移（已完成） |  |
| `FILE_MAP.md` | 项目文件功能地图 |  |
| `MULTI-WINDOW.md` | Copilot 通道多窗口并行（`agent.copilot_windows`） |  |
| `POWER_AUTOMATE_DEV.md` | Power Automate 开发指南 |  |
| `POWER_AUTOMATE_VERIFIED.md` | Power Automate operationId 实测状态 |  |
| `PROVIDER-deepseek-web.md` | 用 DeepSeek 网页版当免费/备用模型的三种接法（调研 + 我们的插件） |  |
| `QUICKSTART.md` | 快速使用手册（一页版） |  |
| `REVIEW-2026-11-agent-session.md` | CopilotBridge Agent 代码审查报告（真实缺陷清单） |  |
| `TEAMS-BOT.md` | Teams 网页版 IT 应答机器人（teams-bot） |  |
| `TEAMS_AUTOMATION_OPTIONS.md` | Teams 自动回复：已试方案、结果与受限点 |  |
| `TECHNICAL.md` | Gantry 技术文档 |  |
| `TODO.md` | Gantry 治理 TODO |  |
| `TRAY.md` | 托盘与后台启动 |  |
| `VERSIONING.md` | 版本规则与协作流程 |  |
| `audit-pyshot.md` | 审计报告：用 pyshot 任务（388 步 / 11.3 小时）反查 CBP 的 Copilot 利用效率 |  |
| `gantry-mindmap.md` | Gantry 结构与流程 Mindmap |  |
| `gantry-mindmap.svg` | 静态图片资源。 |  |
| `quickstart-en.png` | 静态图片资源。 |  |
| `quickstart-zh-TW.png` | 静态图片资源。 |  |
| `quickstart-zh.png` | 静态图片资源。 |  |

## skills/

Agent 技能定义、模板与示例。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `debug-agent-task/SKILL.md` | Agent 任务异常排查 |  |
| `gantry-tool-plugin/SKILL.md` | Gantry 工具插件开发 |  |
| `power-automate/SKILL.md` | 生成 Power Automate 工程 |  |
| `power-automate/examples/outlook-daily.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/examples/sharepoint-to-teams.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/examples/teams-by-name.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/examples/teams-notify.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/templates/apis.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/templates/connections.json` | 数据/配置/示例 JSON。 |  |
| `power-automate/templates/flow-definition.json` | 数据/配置/示例 JSON。 |  |
| `self-upgrade-github-release/SKILL.md` | 自升级后推送 GitHub |  |
| `self-upgrade-task/SKILL.md` | 自升级任务（改服务自身代码） |  |
| `webui-change/SKILL.md` | WebUI 修改规范 |  |

## .github/

GitHub Actions / 模板配置。

| 文件 | 功能 | 关键入口 |
|---|---|---|
| `workflows/browser-integration.yml` | YAML 配置/工作流。 |  |
| `workflows/ci.yml` | YAML 配置/工作流。 |  |
| `workflows/release.yml` | YAML 配置/工作流。 |  |

