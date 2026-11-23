# 分模块 Code Review（2026-11-23）

7 个并行审查代理按功能拆分覆盖：Agent 运行时 / API 路由 / 浏览器自动化 / 工具系统 /
Office-Excel 重点复核 / 插件与前端 / 基础设施与测试。共审 106 个文件，
**新发现 56 项**（5 high / 25 medium / 26 low），已与 `docs/AUDIT-2026-11-10.md`
和 `REVIEW_REPORT.md` 去重。全部 5 条高危已由人工抽查源码/实跑复现确认。

---

## 高危（5）

### H1. 子代理失败被计为成功 — `gantry/subagents.py:209-219`
`_drive_child` 在 `runner.run()` 返回后**无条件** `patch(child_sid, state="completed")`
且返回 `error=None`。runner 的失败出口（gate_blocked / no_progress /
task_gate_blocked / required_tool_blocked / max_total_steps）返回前已把会话置
failed，此处覆盖回 completed。fanout/orchestrator 以 error 字段判 ok →
验证门被拒、无进展循环中止的子任务被父会话当作成功。
**修复**：仅当当前 state 非 failed/cancelled 时才置 completed，或让 AgentResult
透传失败标记。补测试：gate_blocked 子任务 → fanout ok=false、会话 state=failed。

### H2. 窗口池槽位泄漏可致整个网页通道死锁 — `gantry/browser.py:217-224`
`_window()` 中 `pool.acquire()` 成功后、进入 try/finally 之前，
`_PAGE_CTX.set / _WIN_CTX.set / AGENT_TOOLS.clone()` 任一抛异常时，win 不归还、
`win.lock` 不释放（acquire 内部已持锁）。copilot_windows=1 时等于通道永久死锁，
须重启服务。概率低但影响致命。
**修复**：acquire 成功后立即进入 try/finally，ctx 设置放 try 内。

### H3. memory_write 英文描述默认值与实际相反 — `gantry/tool_i18n.py:331,403`
EN 译文写 `project (default) or global`，实现默认 `global`
（tools_memory.py:45）。EN 模式下模型以为写入仅本工作区可见，实际写进全局、
注入所有工作区提示词——跨工作区信息泄露，且与中文描述（tools.py:623）自相矛盾。
**修复**：改为 `global (default)`；加不变量测试：中文描述/DESC_EN/签名默认值三方一致。

### H4. 插件目录遍历：目录外任意文件读写 + 任意代码执行 — `gantry/plugins.py:318-328,453`
- `PluginContext.read_text/write_text` docstring 承诺"限定在插件目录内"，
  实现却无 `relative_to(self.dir)` 校验，`../../../` 可读写插件目录外任意文件。
- `load()` 中 `entry = (manifest.dir / manifest.entry).resolve()` 只查 `is_file()`，
  恶意 plugin.json 声明 `"entry": "../../x.py"` 即可让服务 exec 目录外代码，
  enable/重启自动加载——任意代码执行可持久化。
**修复**：两处均加 `relative_to(manifest.dir.resolve())` 校验，entry 限定纯文件名。

### H5. .env 带引号值 + 行尾注释时密钥被静默截断 — `gantry/envfile.py:43-45`（已实跑复现）
`PASSWORD="ab#cd" # comment` → 实际加载 `"ab`（引号未闭合判定为不带引号 →
按第一个 `#` 截断）。是 M7 修复的残留变体：修了"值内含 #"，却破坏了"引号后有注释"。
无任何告警，表现为莫名其妙的认证失败。
**修复**：按"闭合引号对 + 其后仅空白/注释"正则解析；补变体测试。

---

## 按模块 findings

### 1. Agent 运行时与会话编排（19 文件，1H/4M/4L）

| 级别 | 位置 | 问题 |
|---|---|---|
| H | subagents.py:209-219 | 见 H1 |
| M | agentrun.py:623-640 | run_agent 不捕 CancelledError：客户端断开/服务停机时会话永久停在 running，auto_resume 捞不到 |
| M | orchestration.py:175-187 | adversarial_audit 的 gather 无 return_exceptions：一名审计员抛错即整轮崩溃、其余审计会话悬挂 running |
| M | projection.py:57-62 | `ci == int(payload.get("conversation_index") or ci)`：index=0 或缺失时 `or ci` 使条件恒真，跨代 step_done 误收尾，污染投影一致性校验 |
| M | auto_team.py:117-127 | agent_id 回写用 `agents[min(len(updated),...)]`：updated 含未选中项导致序号错位，B 拿到错的 agent_id |
| L | error_taxonomy.py:34-48 | 状态码裸子串匹配："24000 字节"含"400"、"14290ms"含"429" → 误判 rate_limit/permission 并改变重试决策 |
| L | task_verify.py:320-321 | 未知验收类型返回 ok=True+manual=True：类型名拼错时验收条件静默失效 |
| L | lifecycle.py:55-62 | schedule() 的 Timer 触发后不从 _pending 移除：每种 kind 只能排队一次，之后点击无反应 |
| L | interrupts.py:62-64 | waiter.cancel() 后从不 await：被取消协程悬置，Task was destroyed 泄漏 |

### 2. API/路由/服务层（10 文件，0H/4M/3L）

| 级别 | 位置 | 问题 |
|---|---|---|
| M | routes_core.py:549-557 | probe 端点把完整 traceback[-2500:] 放进 JSON 响应；多处 str(exc) 直出 SSE/HTTP 错误，泄露内部路径/堆栈 |
| M | routes_config.py:49,453 | `/v1/agent/debug/page` 重复注册：第一个生效，截图版被永久遮蔽，模块属性指向被遮蔽函数，测试打桩失真 |
| M | routes_sessions.py:99-121 | session() 端点事件循环内同步 sqlite 6+ 次查询，SessionStore.get 整行读含 132MB 级 recent_steps 才 pop |
| M | api.py:286 | 全局无请求体大小限制：大 messages/附件 base64 全量进内存 |
| L | routes_core.py:646-657 | import/run 接受任意绝对路径 zip（M12 URL 白名单的文件侧同类缺口） |
| L | routes_sessions.py:694-706 | agent_answer check-then-act 竞态：并发第二个请求抛 InvalidStateError 变 500 |
| L | routes_sessions.py:659-691 | SSE 订阅无连接数上限：本机进程可对每会话无限建 EventSource 长连接 |

### 3. 浏览器自动化（8 文件，1H/2M/7L）

| 级别 | 位置 | 问题 |
|---|---|---|
| H | browser.py:217-224 | 见 H2 |
| M | downloads.py:190-210 | 大小上限在整文件读入内存之后才检查：blob/data URL 全量 base64、http 全量 body()，超大下载先打爆内存 |
| M | fileview.py:144 | `read_bytes()[:MAX]` 全量读入再截断：多 GB 文件命中"show me"时主进程 OOM 风险 |
| L | owa.py:173-174 | read 拼 KQL 未转义引号（search 有转义），两个入口策略不一致 |
| L | owa.py:184-186 | 按记录时刻下标点击搜索结果，新邮件插入导致下标漂移、读错邮件 |
| L | owa.py:153-156 | owa_read_current 不做 owa_page 空检查，报错与真实原因无关 |
| L | page.py:142-143 | new_chat 失败分支 goto 无 try 包裹，与 149 行重试分支容错不一致 |
| L | page.py:880-886 | 非 data-URL 字符串当裸 base64 解码写盘上传（validate=False），输入不完全可信 |
| L | copilot_delete.py:92-100 | URL 片段直接插 CSS 属性选择器，含 `"`/`]` 时选择器注入 |
| L | research.py:67-73 | wait_for 取消后输入框草稿残留可能误发；run_plugin_task 对 run_agent 完全无超时，挂起永久占窗口槽 |

### 4. 通用工具系统（25 文件，1H/5M/7L）

| 级别 | 位置 | 问题 |
|---|---|---|
| H | tool_i18n.py:331,403 | 见 H3 |
| M | tool_i18n.py:495 | ARG_EN 仍声明 task_run.allow_commands（参数早已移除）→ EN 模式 TypeError 或误判"命令已获准" |
| M | 各 write 插件 + kb_ingest | write_text/apply_patch/replace_text/replace_lines/notebook_edit/kb_ingest 全部非原子 open('w') 截断写；notebook_edit docstring 误称"原子写回" |
| M | kb_ingest.py:240; kb_ocr.py:257 | extract_text/ocr 截断 200 万字符无警告：导入大文档丢内容无任何信号 |
| M | tools_extra.py:472-482 | job_kill 不杀进程树（孙进程存活、_pump 挂起）；jobs 表只增不删；job_output 无偏移参数每次重返回开头 20KB |
| M | tool_plugins/copy_path.py:15-20 | 目录合并覆盖（dirs_exist_ok）不报告、被覆盖文件无快照不可回滚 |
| L | tool_i18n.py:32-42 | EN 描述 1500 字符 vs 实际 plain_code_limit 6000，模型会切无意义小块 |
| L | tool_i18n.py:425 | ARG_EN grep 默认 200 vs 实际 100 |
| L | kb_semantic.py:238-246 | embedder 返回数量不足时 zip 静默丢块，索引不完整 |
| L | tools_extra.py:239 | grep 行截断无标记；scanned_files 统计口径误导（跳过的大文件也计入） |
| L | tool_plugins/replace_lines.py:44-49 | CRLF content 不预处理 → 写出 \r\r\n 坏行尾 |
| L | tool_plugin_loader.py:32-37 | 加载失败回滚会把已有工具的描述一并删掉 |
| L | memory.py:127-154 | 并发 remember 撞唯一索引时 IntegrityError 裸漏出工具层 |

### 5. Office/Excel 重点复核（本次新改代码，7 文件，0H/4M/8L）

| 级别 | 位置 | 问题 |
|---|---|---|
| M | excel_worker.py:315 | 行协议编码不对称：父写 UTF-8 字节，serve() 用 stdin 文本模式按 locale 解码——legacy 代码页（cp936）机器上中文表名必坏，本机 ACP=UTF-8 掩盖 |
| M | tools_office.py:808-815 | read_csv 不捕 _csv.Error（单字段>128KB）与 LookupError（非法 encoding 名），裸异常漏出（已实测） |
| M | tools_office.py:801 | gb18030 兜底零校验：可消化 UTF-16LE/latin-1 高字节流，乱码当成功返回 |
| M | tools_office.py:206-219 | _stop() 只靠 Job Object KILL_ON_JOB_CLOSE：POSIX/assign 失败时卡死 worker 不杀、内存上限失效；assign 返回值被忽略 |
| L | excel_worker.py:251-256 等 | 三处缓存指纹缺 st_ino：同 tick 同尺寸覆盖写（FAT/exFAT）或 utime 伪造可命中 stale |
| L | tools_office.py:206-219 | _stop() 不关 stdout：每次重启泄漏管道句柄（实测 ResourceWarning） |
| L | tools_office.py:833 | 写路径无串行化：write_csv 截断写与并发 read_csv 交错读半截；并发 write_excel last-wins 静默丢更新 |
| L | xlsx_patch.py:246-258 | 快路径 2× 文件大小内存峰值，且跑在无 Job Object 上限的 agent 主进程 |
| L | excel_worker.py:75-84 | calamine 单请求最多 from_path 同一文件 3 次，拖慢大表首块 |
| L | excel_guard.py:249 | valued_cells 预算下纯样式炸弹不再快速 truncated，worst case ≈ 表数×8s，需确认是否有意 |
| L | tools_office.py:820-842 | write_csv 对 =/+/-/@ 开头单元格零转义：CSV 公式注入载体 |
| L | tests | worker 崩溃恢复路径零覆盖；.xls 断言依赖 openpyxl 报错文案（脆） |

### 6. 插件与前端（20 文件，2H/3M/2L）

| 级别 | 位置 | 问题 |
|---|---|---|
| H | plugins.py:318-328 | 见 H4（目录外文件读写） |
| H | plugins.py:453 | 见 H4（目录外 entry → 任意代码执行） |
| M | prompt_lang.py:127,92-187 | 英文协议与中文原文漂移：1500 vs 6000 字符阈值；EN 版缺【最终答复格式】整段 |
| M | webui/js/apistate.js:29 | JSON.parse 无 try/catch：非 JSON 响应变 SyntaxError，丢失 URL/状态码上下文 |
| M | i18n.py:64-70 | "不存在$→not found" 兜底模式假阳性："该文件已被移动或不存在"→中英混杂错译 |
| L | webui/js/followload.js:258 | SSE token 走 query 参数：进入访问日志/浏览器历史，需确认日志脱敏 |
| L | plugins/schedule/plugin.py:29-33 | _parse_every 不拒 nan/inf：float("nan") 使任务静默永不触发 |

（前端 innerHTML 抽查未发现新 XSS；SSE 会话守卫、部分快照合并均正确。）

### 7. 基础设施、配置与测试（17 文件，1H/3M/3L）

| 级别 | 位置 | 问题 |
|---|---|---|
| H | envfile.py:43-45 | 见 H5（已实跑复现） |
| M | api.py:449-452 | token 脱敏过滤器挂 logger 层：gantry.* 子 logger propagate 的记录不经过它，?token= 仍可明文落盘（已最小复现） |
| M | providers.py:243-246 | net_error_text 把完整代理 URL（可能含 user:pass@）写进会落盘的错误文本；测试还把泄露固化为断言 |
| M | updater.py:297-305 | 更新备份只增不减：每次全量代码副本无限堆积 |
| L | provider.py:66 | _PROVIDER_LOCKS 按 sid 只增不删，大量一次性 sid 内存单调增长 |
| L | tests/test_message_queue.py:64,100 等 | 固定 asyncio.sleep(0.05) 盲等同步，CI 有 flake 风险 |
| - | assets/config.yaml | 不存在（配置模板实为 config.example.json，其密钥引用做法正确） |

---

## 修复优先级建议

**立即（高危，建议下个提交前）**：H1–H5，每条改动都很小（几行到十几行）。

**近期（medium）**：
1. 安全类：H4 同类（routes import/run 绝对路径）、api.py 脱敏过滤器下沉 handler 层、providers 代理 URL 脱敏、probe traceback
2. 正确性类：agentrun CancelledError 收尾、adversarial_audit gather、projection 代次匹配、auto_team 序号、routes debug/page 重复注册
3. 本次 Office 改动合并前必修：M×4（编码对齐、_csv.Error、_stop 兜底、Job Object 检查）

**排期（low）**：原子写统一改造（write 插件族 + kb_ingest）、缓存指纹加 st_ino、
job_kill 进程树、备份淘汰、i18n 数值一致性测试等。

## 测试覆盖缺口（测试绿但线上坏的路径）

- 子代理 gate_blocked → fanout 判失败的端到端（已补 test_subagent_gate_blocked）
- projection 跨代 step_done 匹配（已补跨代 + 唯一 inflight 两例）
- classify_text 数字误报（24000/1429）（已补）
- auto_team 乱序回写（已补）
- envfile 引号+行尾注释变体（已补）
- worker 崩溃/半行响应/serve 协议级（已补）
- ARG_EN 键与函数签名一致性（已补反向扫描测试）

---

## 修复状态（2026-11-23 全部 56 项已修）

全部 findings 已修复，改动在工作区（未提交）。按模块：

| 模块 | findings | 状态 | 关键验证 |
|---|---|---|---|
| Agent 运行时 | 9 | ✅ | 新增 test_subagent_gate_blocked、test_agentrun_cancel；projection 语义折中为「唯一 inflight 才收尾、跨代歧义告警」以兼容旧事件 |
| API/路由 | 7 | ✅ | body 上限中间件实测 9MB→413、33MB→413；debug 截图版改名 /v1/agent/debug/snapshot（全仓无其他引用） |
| 浏览器自动化 | 10 | ✅ | 窗口池异常路径 release；下载 JS 侧预检大小；fileview 部分读 |
| 通用工具 | 13 | ✅ | `_atomic.py` 原子落盘助手覆盖 5 个写插件 + kb_ingest；ARG_EN 反向扫描测试（顺带补回 write_csv.encoding 的注册声明） |
| Office/Excel | 12 | ✅ | serve 改 stdin.buffer UTF-8；write 路径模块级锁 + 原子写；xlsx_patch 流式复制（修复 ZipInfo header_offset 被覆写的坑）；超时 <50ms 直接判超时（消除 1ms 超时测试的时序抖动） |
| 插件与前端 | 7 | ✅ | 插件目录 relative_to 校验 + entry 纯文件名正则；prompt_lang EN 补【最终答复格式】；apistate.js JSON.parse 防护（node --check 通过） |
| 基础设施 | 6 | ✅ | envfile 引号+注释正则；token 脱敏过滤器下沉 handler 层（含 gantry.* 子 logger 落盘测试）；代理 URL 剥离 userinfo；备份保留 5 份 |

**全量测试**：1964 用例，失败项与合并基线逐项一致（10 个环境类遗留），零新增回归。
