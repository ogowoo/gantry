# Gantry 项目概览 —— AI 开发快速上手

> 目标读者：用 AI 编程助手（Claude Code 等）在本仓库工作的开发者。
> 目的：10 分钟建立全局认识——改哪里、怎么验证、去哪查细节。
> 硬性约定见仓库根 `AGENT.md`（AI 会话自动注入）；本文只做概览，细节走文末索引。

## 1. 一句话定位

用 Playwright 驱动本机 Edge 里的 M365 Copilot 网页，包装成本地 OpenAI 兼容 API
（`/v1/chat/completions`），并内置长任务 Agent（JSON 动作协议 + 工具循环 + 审批/沙箱）。
同时支持任意 OpenAI 兼容 Provider 旁路（DeepSeek/Kimi/OpenAI/Ollama…），周边包括
WebUI 管理台、系统托盘、知识库（KB）、Teams 机器人、插件系统、自更新。

## 2. 运行时形态（一个进程装了什么）

- `start.py` 在**主线程**构造并运行 `uvicorn.Server`（自己持有 server 对象，才能优雅停机/重启）
- 托盘 `gantry/tray.py` 在**工作线程**跑 Win32 消息循环（纯 ctypes，零第三方依赖）
- Playwright 与 Agent 任务全部在 **asyncio** 事件循环里
- 默认 `python start.py`：派生后台子进程（pythonw，argv 带 `--tray-child`，终端可关）；
  `--foreground` 前台跑；`--no-tray` 不要托盘
- 端口：`config.json` → `server.port`（默认 8765）；日志 `data/logs/gantry.log`；
  启动早期崩溃兜底 `data/logs/crash.log`；单实例锁 `data/.instance.lock`
- 鉴权：`api_token` 生效时所有 `/v1/*` 要求 `X-Gantry-Token` / Bearer。
  取值优先级：config.json 实值（字面量或 `${VAR}` 能解析）> `data/api_token`
  （缺失自动生成，`start.py::ensure_api_token()` 统一传播到 `GANTRY_API_TOKEN` 环境变量）

## 3. 请求与任务流转

```
WebUI(webui/ ES 模块) / CLI(gantry/cli.py) / 托盘(tray.py)
      │  HTTP 127.0.0.1:8765
      ▼
gantry/api.py —— FastAPI 应用 + 中间件（token_auth、CSRF origin_guard、请求体限额、语言头）
      │  路由按域拆分：routes_core / routes_config / routes_agents / routes_sessions /
      │  routes_update / routes_soul / routes_providers / routes_oauth / routes_live
      ▼
gantry/bridge.py —— 运行态 facade：配置、状态、浏览器、会话、提供商、插件、日志
      │  ⚠ 关键约定：**模块级可变状态只放 bridge.py**（ctx/pw/WINDOWS/HEADLESS_CURRENT…），
      │   其它模块一律 `from . import bridge as b` 经 b.xxx 读写（测试靠 rebind 打桩）
      ├─ Copilot 通道   browser.py + page.py → Playwright 驱动 Edge → 注入提示词/读回复
      ├─ Provider 通道  providers.py → OpenAI 兼容端点（模型 ID 形如 deepseek/xxx）
      ├─ 本地模型       local_models.py（GGUF 进程内推理，可选）
      └─ Agent 通道     agent.py 主循环 + mixin 拆分：
            agent_prompt.py   系统提示词装配（改 Agent 行为先改这里）
            agent_parse.py    模型输出 → JSON 动作协议（容错正则解析）
            agent_text.py     token 估算 / 上下文压缩
            agentrun.py       回合编排（等待回答、run_agent 主流程）
            tools.py          工具注册表 / 分派 / 权限门 / 路径沙箱
            tools_*.py        文件 / 执行 / Office / Outlook / 技能 / 记忆 / 自升级 工具
            session_store.py  SQLite(WAL) 会话 / 消息 / 步骤持久化
            snapshots.py      改动前文件快照 → 整任务回滚
      ▼
SSE（GET /v1/agent/sessions/{sid}/events）实时推给 WebUI；X-Gantry-Lang 请求头定语言
```

## 4. 关键模块速查

| 模块 | 职责 | 注意 |
|---|---|---|
| `api.py` / `routes_*.py` | HTTP 层：全部端点 | 新端点放进对应域的 routes_* |
| `bridge.py` | 运行态 facade + 模块级可变状态 | 其它模块经 `b.xxx` 读写，不另设全局 |
| `browser.py` / `window_pool.py` | 浏览器生命周期 / 多窗口池调度 | 状态仍放 bridge.py |
| `agent.py` | Agent 循环、状态治理、验证门 | mixin 拆在 agent_{prompt,parse,text}.py |
| `tools.py` / `tools_*.py` | 工具注册、权限门、路径沙箱 | 新工具注册进 registry |
| `session_store.py` | SQLite 会话库（WAL） | 会话/消息/步骤都在这里 |
| `snapshots.py` | 工作区快照与回滚 | 改动前捕获，同一路径保留最早态 |
| `providers.py` | OpenAI 兼容 Provider 接入 | 模型 ID 约定 `provider/model` |
| `lifecycle.py` | 优雅停机/重启 | 先回响应、延时触发、超时硬退兜底 |
| `tray.py` | 系统托盘（Win32 ctypes） | 动作全部走本地 HTTP，不 import 服务内部 |
| `updater.py` / `routes_update.py` | 自更新（git/zip、备份、回滚） | 备份在 data/updates/backups |
| `kb*.py` | 知识库：语义检索/OCR/重排 | 可选能力 |
| `plugins.py` + `plugins/` | 插件系统（teams-bot 等） | 启停状态在 data/plugins.json |
| `webui/` | 管理台（ES 模块） | 共享可变状态只放 `js/globals.js` |
| `envfile.py` | `.env` 加载 + `${VAR}` 占位符解析 | 优先级：系统环境 > .env > config 字面量 |
| `version.py` | 版本号**单一来源** | 别处一律引用，勿硬编码 |

## 5. 数据与文件位置

- `config.json`：运行配置（缺失时 `install.py` 从 config.example.json 复制；agent 段约 30 项开关）
- `.env`：密钥（不入库），config 里用 `${VAR}` 引用
- `data/`：`agent.db`（会话库）、`api_token`、`logs/`、`snapshots/`、`uploads/`、`.instance.lock`
- `EdgeProfile/`：Edge 登录态（含 Cookie，**勿外泄**）
- `workspace/`：Agent 默认工作区（`agent.workspace` 可改）

## 6. 开发与验证命令

```powershell
python -m unittest discover -s tests              # 回归测试（改解析/提示逻辑必跑）
python scripts/run_tests_chunked.py --timeout 180 --skip test_liveview   # CI 同款分块跑
python scripts/invariants.py                      # 跨模块不变量检查
node --check webui/js/*.js                        # 前端语法门
python scripts/cross_module_refs.py               # 前端跨模块引用检查
python scripts/css_style_diff.py --record/--compare   # 改样式前后逐元素比对，必须 0 差异
python start.py --foreground                      # 前台起服务
python start.py --tray-selftest                   # 托盘 Win32 自检（真的建图标）
python scripts/bump_version.py beta --commit --tag --push   # 发版（tag 触发 Trusted Release CI）
```

## 7. 硬性约定速记（完整版见根 `AGENT.md`）

1. 改代码闭环：validate_python + run_unittest 双 passed 前不得宣称完成
2. WebUI 是 ES 模块结构：共享可变状态只能放 `webui/js/globals.js`；`/ui/{asset}` 有白名单
3. 系统提示词在 `agent_prompt.py`；解析容错在 `agent_parse.py`（改必加回归用例）
4. Outlook COM 必须走 `tools.py` 的 `_ComWorker`（否则 0x80004004 E_ABORT）
5. 保持简单：不为单次用途新增抽象；改动可追溯；以可检查的成功条件驱动
6. 安全开关默认保守：`allow_python` / `allow_delete` / `allow_outlook_send` 默认全关

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| `AGENT.md`（根） | 项目约定与硬规则（AI 会话自动注入） |
| `docs/ARCHITECTURE.md` | 架构基线（分层、组件图） |
| `docs/TECHNICAL.md` | 技术细节汇编 |
| `docs/FILE_MAP.md` | 文件级功能地图（大而全） |
| `docs/SECURITY.md` | 安全模型与威胁边界 |
| `docs/VERSIONING.md` | 版本规则与发布流程 |
| `docs/UPDATES.md` | 自更新机制 |
| `docs/QUICKSTART.md` | 安装与快速开始 |
| `docs/CSS-SPLIT.md` / `docs/ESM-MIGRATION.md` | 前端样式拆分 / ES 模块迁移 |
| `README.zh-CN.md` | 完整功能清单与 API 表 |
