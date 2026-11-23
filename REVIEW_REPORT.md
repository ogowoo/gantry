# Gantry 项目全面 Review Report

- 项目：gantry 0.11.0-beta.47（把 M365 Copilot 网页端桥接成本地 OpenAI 兼容 API + Agent 运行时）
- 仓库：C:\WorkSpace\copilotbridge_agent（main，与 origin 同步，工作区干净）
- 审查日期：2026-11-23
- 方法：pygount 规模统计 + 全量安全 grep + 测试套件实跑 + 4 个独立 reviewer 子代理按模块层深度审查（核心 Agent 循环 / HTTP-API 层 / 工具执行层 / 浏览器-DOM 层），关键发现由主审复核源码确认。

---

## 一、总体结论

**质量等级：B+（工程成熟度较高，安全姿态在同类本地桥接项目中偏上，但存在若干"能力声明与实际不符"的落差和少量真实漏洞）**

正面：
- 安全设计有体系且落地：非回环监听强制 api_token（api.py:42-46）、CSRF/Sec-Fetch-Site 防护、token 常量时间比较、无 CORS 中间件、updater 校验 SHA256、文件工具层 resolve+relative_to+denylist+审批门扎实。
- 测试扎实：250 个测试模块、约 1913 个测试函数，全部通过（0 失败、0 超时，总耗时 392s）。
- 注释质量高，大量"实测踩坑"记录；幂等箱、身份核验、窗口复用、审计分级都是正确方向。
- 测试基建有意识：run_tests_chunked 解决整体跑挂起的问题；invariants.py、audit_* 系列脚本齐全。

主要短板：
- 三处"宣称的安全语义不成立"：run_code "只读沙箱"、task_verify 的 allow_commands 模型自控、excel "探测都进 worker"。
- 自更新链路的完整性校验可静默降级（无 digest 放行、tag 失败降级分支头）。
- 资源泄漏集中在取消路径（标签页、CDP 会话、草稿残留）。

## 二、规模与结构

- Python：505 个文件，约 5.9 万行代码（gantry 包本体约 4.2 万行）；前端 JS/CSS/HTML 约 8.8 万行（含生成文件）。
- 核心大件：teams_api.py (1903 行)、page.py (1594)、agent.py (1560)、bridge.py (1365)、tools_extra.py (1126)、teams_dom.py (1083)、agentrun.py (1038)。
- 模块命名有成对近似（orchestrator/orchestration、teams/teams_api、provider/providers），但 docstring 都写明了职责边界，非重复代码。
- 提交节奏健康（近 30 天 49 个提交，git 历史线性清晰）。

## 三、HIGH 级问题（须优先修复，已逐条复核源码属实）

### H1. routes_sessions.py:76 — 会话文件读取路径前缀绕过（目录穿越，任意文件读）
`str(target).startswith(str(root))` 是字符串前缀比较。workspace 为 `C:\WorkSpace\proj` 时，`path=../../proj_secret/key.txt` 解析为 `C:\WorkSpace\proj_secret\key.txt`，前缀判断通过，服务进程可读的任意兄弟目录文件被返回。仓库内 atrefs.py:31-35 已有正确范例。
修复：`target.relative_to(root)`（Windows 下 normcase 后比较）。

### H2. run_code.py:93 — "只读沙箱"名不副实
子进程无 `-I`、无 cwd 限制、无任何文件系统隔离；PRELUDE 里的只读代理只约束 `__rpc` 工具调用，对子进程直接 I/O 完全无效。LLM 代码可写工作区外任意文件；而 tools.py:586-588 给模型的描述声称"只读、无写入"。
修复：描述与 docstring 改为与 run_python 同级的"任意代码执行"警示；若真要只读语义需受限账户/容器/AppContainer。

### H3. task_verify.py:80 + tools_extra.py — shell=True 且 allow_commands 由被验证方控制
`subprocess.run(command, shell=True)` 的 command 来自模型自填的验收条件；task_run 的 allow_commands 也是模型工具参数——"命令检查默认关闭、调用方显式允许"把钥匙交给了被验证方。shell=True + 继承父进程环境，`set`/`env` 即可把凭据泄进模型上下文；task_verify.py:197-198 的 f-string 拼命令可被引号逃逸。
修复：allow_commands 改 config 白名单或强制一次性审批；`shlex.split`+参数列表替代 shell=True；裁剪 env；输出落库前做凭据模式扫描。

### H4. task_gate.py:25-26 — 验收门数据库路径与工具层不一致
verify_task_plan 硬编码默认 `data/tasks.db`，无视 `task_db_path` 配置。用户配置自定义路径后，任务写在自定义库、验收门读默认库（永远空）→ 最终验收门被静默跳过。
修复：与 tools_extra._task_store 统一路径解析，或由 agentrun 注入同一 store。

### H5. owa.py — 完全无页面互斥 + 启动缺 ensure_browser
Teams 层有 `_PAGE_LOCK`，OWA 层没有。并发 search/send 共享键盘焦点，最坏后果是**发错收件人**；owa_open 在浏览器未启动时抛 AttributeError。
修复：与 teams_common 同款 asyncio 锁包住复合操作；入口先 ensure_browser_ready。

### H6. updater.py:421-424 — 无 digest 时整体跳过完整性校验；371-379 — tag 解析失败静默降级分支 HEAD
GitHub release 未上传 digest 时校验被跳过，zip 直接替换服务自身代码（自更新=RCE 通道只依赖 HTTPS）；tag 抓取失败会安装"当前分支头"且警告只进日志。
修复：digest 缺失默认拒绝（显式 force 才放行）；版本明确时禁止分支头兜底并在结果中标记 fallback。

### H7. tools_office.py:169,214 — excel_preview/excel_read_chunk 的 inspect 在父进程执行
`_read_shared_strings` 把 sharedStrings.xml 全量 ET.fromstring 进内存，无大小/内存预算，绕过 worker 的全部限额。恶意 xlsx 可直接撑爆桥接主进程（DoS），且与"探测都进 worker"设计意图矛盾。
修复：两个接口与 read_excel 一样走 excel_worker 的 inspect action；给 sharedStrings 加字节预算。

### H8. routes_config.py:637-672 — download_local_model 冻结事件循环 + 无 URL 校验 + 无大小上限
urllib 同步调用直接放 `async def` 端点里（最长 3600s 冻结整个事件循环）；`file:///` 也可读；无磁盘上限。
修复：`asyncio.to_thread`；http(s) scheme 白名单；按 Content-Length 和累计字节设上限。

## 四、MEDIUM 级问题（精选，完整清单见各层附录）

| # | 位置 | 问题 |
|---|------|------|
| M1 | task_plan.py:518-589 | update_task/note/replan 读-改-写跨连接无事务，并行子代理互相覆盖（整计划 DELETE+INSERT 重写放大丢失面） |
| M2 | task_verify.py:137,160,202 | 模型自填脏参数 `float("abc")` 未捕获，可整崩 run_plan（gather 无 return_exceptions） |
| M3 | orchestrator.py:154-161 | failure_reason 引用循环残留 owner 变量，报错张冠李戴 |
| M4 | orchestrator.py:183-188 | 子代理超时取消后子会话永久停在 running（CancelledError 不被 except Exception 捕获） |
| M5 | subagents.py:130,236-240 | finally 写全局 ACTIVE_SESSION_ID/AGENT_TOOLS，并行父会话串扰 |
| M6 | queue.py:56-69 | wake.clear() 抹掉 enqueue 的 set → 消息 worker 永久挂起（丢失唤醒） |
| M7 | envfile.py:42-46 | 带引号且值含 `#` 时被截断（先剥引号后判断，逻辑恒错）——密钥类值常含 # |
| M8 | tools_extra.py:281-293 | web_fetch SSRF 检查可被重定向绕过，响应体无内存上限（应 stream+cap） |
| M9 | teams_api.py:949 | 携带 Bearer 令牌的 httpx `verify=False`（8 个候选 host）——中间人可截令牌 |
| M10 | teams_api.py:31-120 | context 级抓包钩子常驻所有页面、永不清空，响应体含 skypeToken |
| M11 | teams_api.py:227-250 | skypeToken 缓存无过期检查，过期后每次 401 再走 UI 回退 |
| M12 | power_apps_ui.py:127-139 / power_automate_ui.py:330-346 | goto_path 绝对 URL 无域名白名单，SSO 浏览器可被导航到任意 URL（同仓库 open_portal 已做对） |
| M13 | power_automate_ui.py:38-54 | Entra 会话 cookie 明文落盘 data/pa-auth.json，无加密无 ACL；`_cookie_allowed` 子串匹配可绕过 |
| M14 | lifecycle.py:138-179 | "exec 重启"实为 Popen，存在端口竞态（address already in use → 新进程启动即死，叠加托盘重启假阳性） |
| M15 | teams_send.py:237-247 | 发送超时被取消时输入框残留半条草稿，后续任意 Enter 可能误发 |
| M16 | browser.py:158-184 / teams_api.py:1186-1219 | 取消路径泄漏新标签页 / CDP 会话（detach 不在 finally） |
| M17 | routes_core.py health | /health 不经 token 鉴权泄露 Copilot 页面 URL（0.0.0.0+token 部署时局域网可达） |
| M18 | routes_config.py:286-296 | GET /v1/config/raw 原样返回明文 api_token 与 provider api_key |
| M19 | routes_sessions.py:281-311 | delete_session 接受外部 urls 列表并驱动 SSO 浏览器导航（浏览器即 SSRF） |
| M20 | updater.py:312 vs 580 | apply 持锁、rollback 不持锁，可并发产生混杂文件树 |
| M21 | downloads.py:169-196 | 下载无大小上限 + 允许任意 http(s) href（内网面） |
| M22 | xlsx_patch.py:206-215 | 新行插入 O(N×文件大小)；ROW_RE 不匹配自闭合 `<row/>`，会产生重复行号损坏文件 |
| M23 | fileview.py:62-80 | `_resolve` 对 `a/../../x` 型 token 边界防护不足（400KB 工作区外读取） |
| M24 | page.py:34-43 | `visible()` 的 loc.count() 无 try，瞬态页面错误可中断 send 主流程 |
| M25 | api.py:338-349 | CSRF 本地源判定对"回环但未写端口"的 Origin 直接放行（本机 80 端口恶意服务可伪造） |

## 五、LOW 级问题（归类摘要）

- 死代码/复制粘贴残留：agent.py:822 连续两行相同赋值、agent_parse.py:95-99 for-else 死代码、agent_prompt.py:55-62 tools 段重复登记三次（每回合多两次大 JSON 序列化）、session_store.py:886 函数中部的假 docstring、providers.py:251 堆叠双 @staticmethod。
- 无界增长：agent.py:1269/1343 只读缓存无 LRU 上限；tools_extra.py:388-404 后台 job 输出无上限。
- 健壮性小疵：run_code.py:141-142 JSON 结果判断逻辑错（数组被当字符串）；excel worker limit 参数 int() 无上限（CLI 直跑不受 clamp）；read_text line_start 模式整文件进内存；replace_lines CRLF 处理；browser.py:50-56 忙检测异常按"不忙"兜底（方向反了）；tray.py 重启两阶段判定缺失；teams_dom.py:541-551 登录墙文本误判中文正文；teams_mentions.py:180 可视区 900px 硬编码；teams_idb.py:356 调用方输入直接进 JS RegExp。
- 提示词层约束过弱：run_python（tools_exec.py:82-98）等同任意代码执行，仅靠提示词"禁止修改项目文件"约束。

## 六、模块评分汇总（1-5，各层 reviewer 独立打分）

| 模块 | 分 | 模块 | 分 |
|---|---|---|---|
| spans/orchestration | 5 | session_store | 4 |
| subproc.py | 5 | security.py | 4 |
| viewport.py | 5 | tools.py / tools_files | 4 |
| teams_logic / teams_outbox | 5 | tool_plugins 文件工具组 | 4 |
| agent.py / agentrun.py / agent_parse / agent_prompt / subagents | 4 | excel_guard / excel_worker / process_guard | 4 |
| bridge.py / api.py / routes_core / routes_agents / routes_update / providers / provider | 4 | browser.py / window_pool / teams_common / teams_mentions / power_platform_ui / tray / lifecycle | 4 |
| **owa.py** | **2** | routes_sessions / routes_config / queue | 3 |
| **teams_api.py** | **2** | task_gate | 2 |
| run_code.py | 2 | fileview / downloads / envfile / xlsx_patch | 3 |
| task_plan / task_verify / orchestrator | 3 | tools_exec / tools_extra | 3 |

最低分集中在：teams_api.py（verify=False、token 永不过期、钩子常驻、CDP 泄漏、诊断聚合重试风暴）、owa.py（无锁、无 ensure_browser）、task_gate.py（配置后即失效）、run_code.py（沙箱声明不实）。

## 七、测试与质量门验证（本次实跑）

- 全量测试：250 模块 / ~1913 测试函数，全部 OK，无失败、无超时（392.3s，run_tests_chunked.py --timeout 240）。
- 安全 grep：凭据硬编码、pickle、os.system 无命中；shell=True 仅 task_verify.py 一处（见 H3）。
- 依赖卫生：requirements.txt 比 pyproject.toml 多 beautifulsoup4/fpdf2/Pillow/pywin32（pywin32 在 optional-dependencies 里有）；建议对齐，避免 pip install . 与 requirements 安装结果不一致。
- TODO/FIXME 仅 15 处，裸 except: pass 仅 2 处。

## 八、修复优先级建议

P0（本周）：
1. H1 路径穿越（relative_to 一行修复）
2. H2/H3 代码执行语义对齐（改描述 + allow_commands 收权 + 去 shell=True）
3. H6 updater 完整性默认拒绝 + 禁分支头兜底
4. H5 owa.py 补锁 + ensure_browser
5. M7 envfile quoted-# bug、M6 queue 丢失唤醒（各几行）

P1（下个迭代）：
6. H4 验收门 DB 路径统一；M1 task_plan 事务互斥；M2 脏参数兜底
7. H7 excel inspect 收进 worker；H8 下载端点 async 化 + 白名单 + 上限
8. M9-M13 Teams/Power 平台层安全项（verify=False、token TTL、钩子清理、goto_path 白名单、pa-auth.json 保护）
9. M14/M15 重启竞态与发送草稿清理；M3/M4 orchestrator 两处

P2（持续）：
10. M16-M25 中危与低危清单、模块评分 ≤3 的重构评估（owa.py、teams_api.py 拆分）。

## 九、附录：审查方法与可信度说明

- 4 个 reviewer 子代理按层独立审查，仅拿到模块清单与检查要点，无实现者上下文；所有 HIGH/MEDIUM 发现带 文件:行号+证据片段，主审对 6 条最高危发现复核源码逐条属实。
- 测试为真实执行（.venv/Scripts/python.exe scripts/run_tests_chunked.py），非静态推断。
- 局限：未做动态渗透（未启动服务实测端口/鉴权行为）；playwright 浏览器层为静态审查；评分为主观但基于明确扣分点。
