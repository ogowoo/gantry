# 更新日志

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
版本号规则与发布流程见 [docs/VERSIONING.md](docs/VERSIONING.md)。

## [Unreleased]

### Added
- 

### Changed
- 

### Fixed
- 

## [11.0.0-preview.69] - 2026-11-05

### Added
- 

### Changed
- 

### Fixed
-

## [11.0.0-preview.68] - 2026-11-05

### Changed

- **P3 · 单条回答有超时**：`answer_timeout_seconds`（默认 60s）包住整条回答链路
  （KB 检索 + 模型回退）；超时返回兜底文案（`reply_when_unknown` 开）并记 `source=timeout`，
  不再让一次卡住的模型调用把整轮拖死。
- **发送前标题校验**：`header_title` 等待 `5s → 2s`（身份校验仍保留，只是更快失败）。

### 实测

- `tests/test_teams_bot.py` 31 条（新增 answer 超时 1 条）；全套 1580（skipped=1）OK；
  不变量 25 条全绿。

## [11.0.0-preview.67] - 2026-11-05

### Fixed

- **P0 · Teams 未登录不再每轮硬撞**：新增 `TeamsAuthRequired`（登录墙专用异常）；
  `GET api/idb-watch` 返回 **HTTP 200** + `{"ok":false,"auth":"expired","hint":...}`
  （此前是 500）；`api/status` 增加 `session.auth` / `retry_at`；cycle 在鉴权失败后
  按 `auth_backoff_seconds`（默认 300s）退避，期间返回 `skipped: auth_backoff`。
  实测：run#1 报"未登录（300s 后重试）"，run#2 返回 `auth_backoff, retry_in=300`。

- **P1 · 发送与轮询解耦**：cycle 只做"扫描 + 回答 + 入队"，独立 worker 串行发送；
  单条 `send_timeout_seconds` 120 → **45s**；`status` 增加 `queue_size` / `sending`；
  `out["queued"]` 记录入队数。发送失败/超时仍写 outbox（幂等不变）。

- **P2 · IDB 增量扫描**：`api_idb_watch(since=)` 只返回 `lastTime` 之后变化的会话
  （JS 侧先过滤再序列化）；插件用 `idb_watermark` 记录水位（只增不减），
  下一轮带上水位，减少跨进程传输与遍历。

### 实测

- 线上：`idb-watch` 200 + `auth=expired`；`status.session={'auth':'expired','retry_at':...}`；
  退避生效。
- 测试：`tests/test_teams_bot.py` 30 条（新增水位/第二轮带水位/鉴权退避 3 条）；
  全套 1579（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.66] - 2026-11-05

### Changed

- **`gantry/teams.py` 解耦完成**：从 3964 行的单体拆成 6 个模块，`teams.py` 只剩
  **169 行 façade**（保留 `__getattr__` 惰性取 bridge，其余全部显式 re-export，
  插件/测试/脚本零改动）：

  | 模块 | 行数 | 职责 |
  |---|---|---|
  | `teams_dom.py` | 901 | 页面/会话 DOM：开页面、标签、等渲染、头部、消息、点会话、`_SEL` |
  | `teams_send.py` | 567 | 输入框、发送、按消息片段/搜索定位会话 |
  | `teams_api.py` | 1904 | authz/令牌抓取、chatsvc 直发、全部只读排障接口、`probe()` |
  | `teams_idb.py` | 364 | IndexedDB 读取（会话/最后消息/成员映射） |
  | `teams_common.py` | 91 | bridge 句柄、页面互斥锁、异常、身份归一化 |

  依赖方向单向：`common ← dom ← send/api`；`idb` 用函数内惰性 import 拿 `teams_open`，
  避免与 façade 循环。跨模块引用用显式 import 补齐（静态扫描确认三个新模块
  **无未解析裸名**）。

### 实测

- 全套 1576（skipped=1）OK；不变量 25 条全绿；teams 相关 89 条测试通过。
- 服务重启后 `11.0.0-preview.65`→新代码：10 个插件全部 `loaded`，`teams-bot`
  仍是 `enabled=false / dry_run=true / send_enabled=false`（replies=0），无 `crash.log`。

## [11.0.0-preview.65] - 2026-11-05

### Changed

- **`gantry/teams.py` 解耦第 1 阶段**（3964 → 3432 行）：
  - 新增 `gantry/teams_common.py`（90 行）：`TEAMS_URL`、页面互斥锁
    （`_PAGE_LOCK`/`PAGE_LOCK_TIMEOUT`/`REPLY_TIMEOUT`/acquire/release/stats）、
    `TeamsError`/`TeamsBusyError`、身份归一化（`_norm_identity`/`_plain_text`/
    `verify_conversation_identity`）；
  - 新增 `gantry/teams_idb.py`（364 行）：`api_idb_watch` / `api_idb_record` /
    `api_idb_conversations` / `api_idb_enumerate` / `api_idb_probe`，
    用函数内惰性 `from .teams import teams_open` 避免循环依赖；
  - `teams.py` 反向 `from .teams_common import ...` / `from .teams_idb import ...`
    保持所有对外名字不变（插件、测试、脚本零改动）。

  后续阶段（未做）：`teams_api`（authz/chatsvc/令牌抓取）、`teams_probe`（排障抓包）、
  `teams_dom` + `teams_send`（DOM 自动化与发送），最终 `teams.py` 只剩 façade。

### 实测

- 全套 1576（skipped=1）OK；不变量 25 条全绿；teams 相关 89 条测试通过。
- `from gantry import teams` 后 `teams.api_idb_watch.__module__ == "gantry.teams_idb"`，
  `teams.acquire_page_lock.__module__ == "gantry.teams_common"`。

## [11.0.0-preview.64] - 2026-11-05

### Fixed

- **`gantry/teams.py` 里有 3 组重复定义**（并发改动留下的死代码，后者覆盖前者）：
  `api_search_probe`（966/1063）、`api_open_by_search`（988/1085）、`_jwt_claims`（1504/2458）。
  用 AST 精确删除前一组（保留带 `wait`/`exp` 处理的新实现），文件 3964 → 3850 行；
  行为不变（定义顺序上本来就只有后者生效）。

### 实测

- 全套 1576（skipped=1）OK；不变量 25 条全绿；teams 相关 89 条测试通过。

## [11.0.0-preview.63] - 2026-11-05

### Added

- **文档补"四种 embedding 后端的依赖成本"对比**（`docs/QUICKSTART.md` 第 11 节、
  `plugins/kb/README.md`、README），给出实测数字：
  - `hash`：0 包 / 0 MB；
  - `http`：Python 侧 0 包，代价是外部 `--embeddings` 服务；
  - `fastembed`：1 包 + ~10 依赖 ≈ **190 MB**（sympy 66 · numpy 50 · onnxruntime 39 ·
    pillow 15 · tokenizers 8 · hf-hub 5…）+ 模型 **91 MB**，纯 wheel 无需编译；
  - `local`（llama-cpp-python）：**25 MB** 轮子 + 一个 GGUF（bge-small 50-90 MB /
    Qwen3-Embedding-0.6B Q8 610 MB），已在跑 `providers.local` 时**不新增任何包**。
  并说明"依赖最少选 hash/http、要单进程不编译选 fastembed、已有 llama-cpp 选 local"。

### 实测

- 体积按 `importlib.metadata` 的文件清单求和（非估算）：fastembed 链路合计 190.1 MB，
  llama-cpp-python 25.1 MB；本机变更后向量 `vectorised=23/23`、模型
  `fastembed:BAAI/bge-small-zh-v1.5`（512 维）。

## [11.0.0-preview.62] - 2026-11-05

### Added

- **向量检索加 numpy 快路径**（可选优化）：`SemanticIndex.search` 在有 numpy 时用矩阵乘算余弦，
  没有就退回纯 Python；两条路径排序一致（新增 `VectorPathParityTests` 保证）。仍然**零新增依赖**：
  向量就存在同一个 `data/kb.db` 的 `chunks_vec` 表里，不需要 Chroma / Milvus / sqlite-vec。
- **`docs/QUICKSTART.md` 两节新内容**：
  - 「11. 向量库：自带的，不用另装」——表结构、增量建向量、四种 embedding 后端与各自需要什么；
  - 「12. 从旧版升级（CopilotBridge → Gantry）」——停止服务 → `git pull` → `python install.py`
    （可选 `--ocr` / `--xiaomi`）→ 可选配置段 → `kb.py index` / `embed` → 启动，
    并给出升级后自检表（version / 插件 / vector-stats / ocr / models）与三条注意事项。
- `README.md` 增「Upgrading from an older install」小节。

### 实测

- `tests/test_kb_semantic.py` 22 条（新增 numpy / 纯 Python 一致性 1 条）；
- 全套 1547（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.61] - 2026-11-05

### Changed

- **旧名清理**：`docs/quickstart-zh/zh-TW/en.png` 与 `docs/QUICKSTART.md` 里的
  "CopilotBridge Agent" 改为 **Gantry**，clone 地址改为 `github.com/ogowoo/gantry.git`、
  目录 `cd gantry`；三张卡片已重新生成。
- **OCR 改成显式可选安装**：新增 `requirements-ocr.txt`（`rapidocr`）与
  `requirements-ocr-xiaomi.txt`（torch/torchvision/transformers + rapidocr）；
  `install.py` 支持 `--ocr` / `--xiaomi` / `--all` / `--no-browser`，装完打印能力检查
  （浏览器 / RapidOCR / Xiaomi 各自 ✓✗ 与下一步命令）。纯文字知识库不需要 OCR。

### Added

- `kb_ocr.available_xiaomi()` / `effective_engine()`：`auto` 在没装 RapidOCR 时**自动改用
  Xiaomi**；两者都没有时报错直接给两条安装命令。
- `GET /v1/plugins/kb/api/ocr` 增加 `xiaomi` 与 `effective` 字段；
  示例：`{"engine":"auto","rapidocr":true,"xiaomi":true,"effective":"auto→rapidocr"}`。

### 实测

- `python install.py --help` 正常；本机 `rapidocr=True`、`xiaomi deps=True`、
  `auto→rapidocr`。
- `tests/test_kb_ingest.py` 43 条（新增可用性 3 条：effective/无 rapidocr 走 Xiaomi/两者皆无）；
  不变量 25 条全绿。

## [11.0.0-preview.60] - 2026-11-05

### Changed

- **`docs/QUICKSTART.md` 更新到 v11**：新增「10. 知识库（OCR + 向量检索 + 对话入库）」、
  「11. 本地模型（可选，不用外部 server）」、「12. 插件（按需启用）」三节，界面速览补 📚 入口，
  顶部标注覆盖版本。
- **三张一页卡片重新生成**（`quickstart-zh.png` / `quickstart-zh-TW.png` / `quickstart-en.png`）：
  「界面速览」新增 4 行（⚙→📚 知识库 / 对话里入库 / 本地模型 / Teams 机器人），
  「常用操作」新增「把内容存进知识库」；布局从"左步骤右两栏"改成"左步骤+常用操作、右界面速览"，
  避免高度差留白；顺手去掉文案里会被转义显示的 `<b>` 字面量。

### 实测

- 卡片生成：三张均成功（604 / 626 / 566 KB），画面无字面量标签、无溢出。
- 图片仍由 `python scripts/make_quickstart_card.py` 生成，改文案后重跑即可。

## [11.0.0-preview.59] - 2026-11-05

### Added

- **使用手册「快速上手」配图**：新增主界面与知识库管理页两张真实截图（`webui/img/`，
  中文/英文手册各一份，带图注），并把 `figure/figcaption` 样式补进 `dialogs.css`。
  `/ui/{asset}` 白名单扩展为 `js|css|img` + `js|css|png|webp|svg`，不变量同步。
- `scripts/make_manual_shots.py`：重新生成手册截图（Playwright，1440×900）。

### Fixed

- **`data/service.log` 归档**：它是**旧版启动器**留下的 uvicorn stdout 日志（16.3 MB，
  最后写入 2026-10-22，代码里已无任何引用），与 `data/logs/gantry.log`（结构化日志，
  `gantry.logcfg` 写入）**不是同一份文件**，只是内容有部分重叠（都是 uvicorn access 行）。
  已移到 `data/logs/legacy/service-20261022.log`，`data/` 根目录不再放日志。

### 实测

- 手册：18 节、2 张图（naturalWidth 1440 全部加载成功）、无前端异常。
- 资产：`GET /ui/img/quickstart-main.png` 200（91 KB）、`quickstart-kb.png` 200（121 KB）。
- 全套 1544（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.58] - 2026-11-05

### Added

- **OCR 预热 `kb.ocr.eager`**：设为 `true` 时插件启动后在后台线程加载引擎（`kb_ocr.warm()`），
  避免第一次表格页干等 30 秒。`config.json` 默认已开。
- **使用手册更新**（`/ui/` → 📖 使用手册）：新增两节 ——
  「知识库（OCR / 向量检索 / 对话入库）」与「内嵌本地模型（不用外部 server）」，
  中英各一份（手册从 16 节变 18 节）。

### 实测

- 预热后表格页导入 **5.6s**（此前首次 35.1s，含 ~30s 模型加载）。
- 手册渲染：18 个 h3、目录含新节、正文含 `kb_add` / `kb.ocr`，无前端异常。
- `tests/test_kb_ingest.py` 40 条（新增 `warm()` 3 条）；不变量 25 条全绿。

## [11.0.0-preview.57] - 2026-11-05

### Added

- **OCR 引擎 `auto`（按页自动选）**：先用 RapidOCR；满足任一条件就转 Xiaomi-OCR-0：
  - 一个字都没识别到；
  - 文本稀疏：`字符数 / 百万像素 < auto_sparse_chars`（默认 40，表格/表单/PPT 常见）；
  - 版面像表格：按垂直中心分行后，有 ≥ `auto_table_rows`（默认 2）行、每行 ≥2 个框。
  Xiaomi 不可用（未装 transformers/torch 或模型加载失败）时**静默回退** RapidOCR 文本；
  两项阈值可在 `kb.ocr` 里调。`config.json` 默认改为 `"engine": "auto"`。

### 实测

- 表格截图：RapidOCR 给出 4 个单元格框 → 判定表格 → 转 Xiaomi（测试用假引擎覆盖）。
- 长正文图片：保持 RapidOCR（不额外花 6 秒）。
- 引擎选择：`auto` 保留，`default`/未知值回退 `rapidocr`。
- `tests/test_kb_ingest.py` 37 条（新增 auto 5 条：富文本/稀疏/空结果/表格框/不可用回退）。

## [11.0.0-preview.56] - 2026-11-05

### Added

- **Xiaomi-OCR-0 接入为可选 OCR 引擎**（0.8B VLM，Apache-2.0；OmniDocBench v1.6 96.83、
  Real5 95.24 第 1、表格 PubTabNet 92.27、公式 UniMER 97.84）。`gantry/kb_ocr.py` 增加
  `engine=xiaomi`：输出 Markdown（表格 OTSL、公式 LaTeX），适合版面复杂的文档；
  `engine=rapidocr`（默认）仍是 0.7s/张的快速路径。配置 `config.json`：

  ```json
  "kb": {"ocr": {"engine": "xiaomi", "xiaomi_model": "SeerRay-Lab/Xiaomi-OCR-0"}}
  ```
- **对话式扩充知识库**：
  - 工具 `kb_add(text?, title?, url?, paths?, category?)`：模型在会话里整理完直接入库；
  - 每条消息新增「📚 存入知识库」按钮：不经模型，把该条消息原文存入；
  - 斜杠命令 `/kb [内容]`：填入"先整理再 `kb_add`"的指令，确认后发送。

### Changed

- 环境升级：`torch 2.10→2.14.0`、`torchvision 0.25→0.29.0`、`transformers 5.3→5.17.0`
  （Xiaomi-OCR-0 官方要求；项目代码本身不 import torch/transformers，升级不影响 Gantry）。

### 实测

- Xiaomi-OCR-0（CPU，bf16）：processor 23.7s + 模型 30.0s 首次加载；312 token 输入 →
  69 token / **6.4s**（约 10.8 tok/s）。一张"标题+两条正文+表格"的中文截图 → 正确输出
  `## 电脑蓝屏处理手册` + 编号要点 + 表格 `<fcel>现象<fcel>处理<nl>…`（OTSL）。
- `tests/test_kb_ingest.py` 32 条（新增 Xiaomi 引擎 3 条、`kb_add` 3 条）；不变量 25 条全绿。

## [11.0.0-preview.55] - 2026-11-05

### Fixed

- **知识库管理页 Esc 关不掉**：`manual.js` 的全局 Esc 处理器漏了 `closeKb()`
  （其余弹窗都加了）。补上后实测：打开 → 按 Esc → 关闭。

### Added

- **OCR 兜底**（`gantry/kb_ocr.py`）：扫描版 PDF 与图片（png/jpg/jpeg/bmp/webp/tif/tiff）
  → **RapidOCR**（ONNX，CPU，PP-OCRv6 det/rec 小模型，`pip install rapidocr`，首次运行自动下载
  约 20 MB）。PDF 只有**没有文本层的页**才 OCR（正常 PDF 不受影响）；没装引擎时报
  `pip install rapidocr`，不会静默入库空文档。

### 实测

- 中文图片两行全部识别正确（"打印机脱机时先重启 Print Spooler 服务 / VPN 连不上请换手机热点试一次"），
  首次 3.93s（含加载），**0.72s/张**（复用引擎）。
- 图片型 PDF（fpdf 嵌图、无文本层）：`extract_text` 1.25s 返回
  `## 第 1 页（OCR） 扫描件测试：打印机脱机请重启服务`。
- `tests/test_kb_ingest.py` 26 条全绿（含图片 OCR、空结果、缺引擎提示、扫描 PDF 回退、
  有文本层 PDF 不触发 OCR）。

## [11.0.0-preview.54] - 2026-11-04

### Added

- **知识库管理页**（`/ui/` → ⚙ 设置 → 📚 知识库）：拖拽/选择文件导入 `docx / pdf / xlsx /
  pptx / md / txt / csv / html`，导入 URL（抓正文），粘贴文本入库，收件箱（`data/kb/inbox/`），
  文档列表 + 删除 + 重建索引 + 检索测试（显示后端与分数）。
  前端 `webui/js/kb.js` + `webui/index.html` 的 `#kbMask`；入口按钮在设置页。
- **纯 Python 提取**（`gantry/kb_ingest.py`）：`python-docx` / `pypdf` / `openpyxl` /
  `python-pptx` / `beautifulsoup4`，不依赖外部服务；`.doc/.xls/.ppt` 明确提示另存；
  扫描版 PDF 无文本层给出可行动错误。导入落 `data/kb/imported/<slug>.md`（front-matter
  带 title/category/source/updated，同名按来源覆盖）。
- kb 插件接口：`POST api/import`（`{name,data_b64}` / `{url}` / `{text,title}`）、
  `POST api/delete`（限制在知识库目录内）、`GET api/inbox`、`POST api/inbox/scan`；
  每次增删自动重建 FTS + 向量索引。
- `tests/test_kb_ingest.py`（21 条）：docx/xlsx/pptx/pdf/html/纯文本提取、URL 抓取（假网络）、
  落盘与 front-matter、Base64 导入、粘贴、删除、越界拒绝、收件箱扫描。

### 实测

- 通过管理页上传 docx → 列表出现 `imported/upload-check.md`；检索 `打印机脱机` →
  `hybrid(local:Qwen3-Embedding-0.6B-Q8_0)`，`《printer》 › 打印服务使用说明 › 常见问题` 0.88。
- `python scripts/verify_kb_page.py`：打开/统计/列表/上传/检索/关闭全通过；`ui_smoke` 通过。
- 单测 1524（skipped=1）OK；不变量 25 条全绿（含 data-act/i18n 覆盖、静态资源白名单）。

## [11.0.0-preview.53] - 2026-11-04

### Added

- **teams-bot 回答改用混合检索**：`Bot.answer()` 不再只查 FTS，而是走
  `gantry.kb_semantic.hybrid`（0.75×余弦 + 0.25×词面覆盖率），配置读 `config.json` 的
  `kb.embedding`（`backend=local` 时用进程内 Qwen3-Embedding）；向量未建/失败自动退回 FTS。
  返回的 `via` 字段可看命中来自 `vector` / `fts` / `both`。
- `GET /v1/plugins/teams-bot/api/answer?q=`：**只算不发**，用于调知识库与阈值
  （返回 `{text, source, would_reply}`，`source` 形如 `kb:《VPN 连不上怎么办》`）。
- `docs/TEAMS-BOT.md` 增"最短路径：让它用知识库回答"（放资料 → 建索引 → 打开 → 单条验证 → 开发送）。

### 实测

- `api/answer?q=公司网络访问不了外部网站` → `source: kb:wifi`（同义改写命中，
  纯 FTS 时只有 0.138 的弱命中）；`q=VPN 连不上怎么办` → `source: kb:《VPN 连不上怎么办》`。
- `tests/test_teams_bot.py` 23 条（新增 hybrid 优先 / 空结果退回 FTS / 无向量层退回 FTS）。

## [11.0.0-preview.52] - 2026-11-04

### Fixed

- **进程内模型不再抛 `Requested tokens (10192) exceed context window of 4096`**。
  `LocalChatEngine` 增加 token 级裁剪（`_fit`）：
  - 超预算时**保留 system + 最近的对话**，丢弃较早的消息；
  - 单条消息过长则按 token 截断（用模型自己的 `detokenize`，不会切坏 UTF-8）；
  - 结果里带 `trimmed: {tokens, budget, dropped, truncated}`，便于观测；
  - `truncate: false` 时给出**可行动的错误**（带 `n_ctx` 与内存代价），而不是原始 llama.cpp 报错。
- 本地 provider 默认 `n_ctx` 4096 → **16384**（实测 +4096 窗口约 +165 MB：4096→2536 MB、
  8192→2701 MB），11k 提示可直接装下。

### 实测

- 17,500 字符（约 1 万 token）提示：修复前直接报错；修复后成功返回，
  但 **CPU 上要 103.8 秒**（prefill ~96 tok/s）——本地 2B 适合短提示的 RAG 问答，
  长系统提示的 Agent 任务建议用外部/云端模型。
- 单测 27 条（`tests/test_local_models.py`，含 6 条裁剪用例）；不变量 25 条全绿。

## [11.0.0-preview.51] - 2026-11-04

### Added

- **内嵌本地模型引擎**（`gantry/local_models.py`）：GGUF 直接跑在 Gantry 进程里，
  `python start.py` 一个进程带上模型；HTTP provider 照旧，可解耦。
  - `LocalChatEngine`：懒加载 / 单飞锁（llama.cpp 上下文非线程安全）/ 空闲卸载 /
    统计（加载耗时、调用数、tok/s）；`chat()` 与 `stream()`。
  - **推理模型兼容**：`split_think()` 拆 `<think>…</think>`；流式输出扣住可能是
    `<think`/`</think` 前缀的尾巴，标签被切成两个 chunk 也不漏出思考段。
  - `LocalEmbedder`：进程内 embedding，实现 `kb_semantic.Embedder` 协议。
  - `warm_eager()`：只预热 `eager: true` 的提供商，后台线程，不阻塞启动。
- `providers.LocalProvider`：与 `ApiProvider` 同构（`complete` / `complete_message` /
  `stream_chunks` / `models`）；`build_providers` 按 `backend: "local"` 或 `model_path` 分派；
  `/v1/models` 自动出现 `local/<model>`。
- `kb.embedding.backend: local`：知识库 embedding 可走进程内 GGUF（不再依赖 18085）。
- `tests/test_local_models.py`（21 条）。

### 实测

- `local/minicpm5-2b`（MiniCPM5-2B Q4_K_M，1.45 GB）进程内：加载 **1.12s**、
  **35.4 tok/s**（n_threads=12），回答无 `<think>` 残留。
- kb 插件 `backend: local`（Qwen3-Embedding-0.6B）：`api/vector-stats` 显示
  `local:Qwen3-Embedding-0.6B-Q8_0`。

## [11.0.0-preview.50] - 2026-11-04

### Added

- **知识库向量检索（语义召回）落地**（用户："把 1 落进仓库"）。`gantry/kb_semantic.py`：
  - `HttpEmbedder`：任意 OpenAI 兼容 `/embeddings`；本机实测 **Qwen3-Embedding-0.6B**
    （`llama-server --embeddings --port 18085`，1024 维，0.04s/请求）。
  - `HashEmbedder`：零依赖兜底（字符 n-gram hashing，L2 归一化），离线与单测可用。
  - `SemanticIndex`：向量存 `data/kb.db` 的 `chunks_vec(chunk_id, model, dim, vec, at)`；
    **只算缺失分块**，换模型/维度自动整体重建；`build(force)` / `search` / `hybrid`。
  - 混合打分：`w_vec × 余弦归一化 + w_cov × 词面覆盖率`（默认 0.75 / 0.25）。
  - `build_embedder(backend=auto|http|hash|off)`：`auto` 探一下端点，不可用返回 `None`
    （调用方退回 FTS，不抛异常）。
- kb 插件：`kb_search(..., mode="auto|fts|vector|hybrid")`（默认 auto）、新工具 `kb_embed`、
  接口 `GET api/semantic` / `POST api/embed` / `GET api/vector-stats`；配置 `embedding.*`。
- `scripts/kb.py`：`embed` / `vector-stats` 子命令，`search --mode fts|vector|hybrid`。
- `tests/test_kb_semantic.py`（21 条）：hashing 后端、增量/强制重建、余弦打分、混合公式、
  后端选择（off/hash/http/auto 探测失败）、插件 mode 分发与 `kb_embed`。

### 实测

- 真实数据（12 分块 / Qwen3-Embedding 1024 维，0.28s 建完）：
  `公司网络访问不了外部网站` 纯 FTS 只有 1 条弱命中（0.138），混合检索 **0.603** 命中
  《Wi-Fi 802.1X 认证失败 › 症状》；`企业微信登录不上` FTS 0 条，混合仍有可解释结果。
- 单测全绿；`api/vector-stats` 显示 `vectorised=12 chunks=12 complete=true`。

## [11.0.0-preview.49] - 2026-11-04

### Added

- **本地知识库独立成插件 `plugins/kb/`**（用户："本地知识库怎么做成插件"）。原来
  `kb_search` / `kb_reindex` 挂在 `teams-bot` 里，现在拆成通用插件：
  - 工具：`kb_search`（带 `cite` 出处）、`kb_reindex`、`kb_stats`；
  - 接口：`GET api/search?q=&n=`、`POST api/reindex`、`GET api/stats`、`GET api/docs`；
  - 面板 `kb-status`；配置 `dir` / `max_results` / `min_score` / `auto_reindex`。
  - `KnowledgeBase` 支持 `kb_dir=` 覆盖目录（索引库默认 `data/<目录名>.db`）。
  - 索引为空时首次搜索自动建索引（`auto_reindex`）。
- `plugins/kb/README.md`：切块/分词/打分/引用/配置说明，含"什么时候该上向量检索"。
- `tests/test_kb_plugin.py`（12 条）：清单、注册、检索/重建/统计工具、四个接口、自动建索引。

### Changed

- `plugins/teams-bot/plugin.json` 的 `contributes.tools` 去掉 `kb_*`（避免与 kb 插件撞名），
  内部仍用 `gantry.kb.KnowledgeBase` 做"知识库优先"作答。

### Fixed

- **`force=True` 重建索引必炸**：原来先 `DELETE FROM chunks` 再删 FTS 行，子查询已经为空
  → FTS 留孤儿行 → 重新插入复用 rowid 时 `sqlite3.IntegrityError: constraint failed`。
  改成**先删 FTS 行（先取 rowid）再删 chunks**；补 `ForceReindexTests` 两条回归。

### 实测

- `tests/test_kb_plugin.py` + `tests/test_kb.py`：33 条全绿；`kb_reindex(force=True)` 连跑两次
  分块数不变、检索仍可用。

## [11.0.0-preview.48] - 2026-11-04

### Fixed

- **沙箱越界报错不再"只讲规则"**：现场 `task-mw1cb7ue`（"用 python 做一个扫雷"，
  `fflm/deepseek-r1-0528:8b`）模型写 `write_text_b64` 到 **`/src/minesweeper.py`**，
  在 Windows 上解析成 `C:\\src\\...`，落在工作区外 → `FS_SANDBOX_ESCAPE`。
  旧报错只说"改用工作区内相对路径"，小模型不会自己把 `/src/x.py` 换算成 `src/x.py`，
  于是原地卡住。现在 `_sandbox_denied()` 会**直接给出可照抄的写法**：

  ```
  建议: 把 path 改成工作区内的相对路径，例如 path="src/minesweeper.py"（会写到 …\workspace\src\minesweeper.py）
  ```

  规则：`/src/a.py` → `src/a.py`；`C:\\tmp\\a.py` → `tmp/a.py`；`..\\..\\evil.py` → `evil.py`。

- **拦掉"能解码但不是文本"的 b64 载荷**：同一个会话里模型给的 `content_b64` 能通过
  Base64 解码，但内容是 `Support do e(eval,path istable are friend a messembos user handle…`
  这种无意义拼接——路径一旦合法就会写出垃圾文件。现在 `_decode_b64` 解出后做控制字符
  比例检查（> 2% 判为伪文本），直接报错并提示"改用 write_text，不要把内容做 Base64"。

### 实测

- `tests/test_sandbox_errors.py`：9 → **13 条**（越界建议 5 条 + 载荷守卫 4 条），全绿。
- 手动核对：中文/代码/换行制表符通过；控制字符 78% / 100% 的载荷被拦。

## [11.0.0-preview.47] - 2026-11-02

### Added

- **Teams 写侧打通**（B 路线完成）：按"最后一条消息原文"搜索 → 点 "Recent messages" 结果
  → 直接打开目标聊天，再走输入框 + 发送按钮。
  - 关键点：**必须用真实按键** `keyboard.type()` 输入（`fill()` 不触发 autosuggest，
    建议列表一直是空的）；
  - 按**人名**搜会打开**联系人名片**而不是聊天（实测 `Lynn Ling` → `Ling, Lynn lynn.ling@…`），
    所以用 IDB 里的 `lastText` 片段来搜，命中的是消息结果，点开即聊天。
  - `teams.api_open_by_snippet()` / `compose_text()` / `reply_to()`；
    实测返回 `{"opened": true, "how": "snippet", "header": "Lynn Ling - DSV …",
    "sendButton": true, "cleared": true, "sent": false}`（演练不发送）。
- 插件发送路径改走 `teams.reply_to(peer, answer, send=True, snippet=lastText)`。

### Changed

- 写侧两道闸：`dry_run`（默认 true，只记录）与新增 `send_enabled`（默认 false，写侧总闸）。
  两个都打开才会真发送。

### 实测

- 打开会话：按片段搜索 → 点消息结果 → 头部变为 `Tony Zhao - DSV` / `Lynn Ling - DSV`。
- 输入框：输入 22 字符 → `sendButton: true` → 清空（`cleared: true`），**未发送**。
- 单测 1396+（teams_bot 18）全绿。

## [11.0.0-preview.46] - 2026-11-02

### Added

- **Teams 机器人改用 IndexedDB 读会话**（B 路线第一步，可用了）：
  `gantry/teams.py` 新增 `api_idb_watch()` —— 从 `Teams:conversation-manager` 的
  `conversations` 表读会话，配合 `Teams:profiles` 把私聊标注成人名。
  实测：**351 个会话、248 个私聊**，`Lynn Ling - DSV` / `Tony Zhao - DSV` 等都能正确命名。
  - 私聊判定用 **id 形态**（`19:<guid>_<guid>@unq.gbl.spaces`），不能用成员数：
    实测 `memberProperties` 是**单个**成员对象（键固定 `relationshipState`/`role`），
    按它的键数判断会把 289 条误判成私聊。
  - 私聊 id 里的两个 GUID 有一个是 **Skype 租户**（从库名 `skypexspaces-<guid>` 取），
    另一个才是对方 → `profiles` 换人名。
- `gantry/teams_logic.py`：新增 `is_direct_id()` / `peer_guid()`（可离线测）。
- 插件读侧改为 `source: idb`：扫最近 50 个会话里的私聊，按 `lastId`/`lastMessageTimeUtc`
  去重，只处理"含 @ 且不是自己发的"消息，答案仍走"知识库优先 → 模型 → 兜底"。

### Changed

- 插件新增 `send_enabled`（默认 **false**）：写侧（切到目标会话再发）还没打通，
  当前只把"本来会回什么"写进 `status.last_cycle.would[]`。
- 排障接口积累（全部**只读**）：`api/probe-api`、`api/hook-capture`、`api/gtm`、
  `api/idb-watch`、`api/idb-enum`、`api/open-search`、`api/open-url`、`api/cookies-probe` 等。

### Fixed

- `_cfg` 优先级修正：**运行时覆盖 > 插件配置 > 默认值**（之前 config 压过 overrides，
  导致 `api/settings` 改了不生效，`run` 一直 `skipped=disabled`）。
- `max_replies_per_cycle` 在 dry_run 下不生效（用"已发送数"计数，dry_run 永远是 0）→ 改为
  "已处理数"。

### 未完成（写侧）

- 切到指定会话三条路都失败：点会话项（命中头像/资料卡）、搜索框（`AUTOSUGGEST_INPUT`
  填了不弹建议）、深链（4 种 URL 形式都不切换）。
- chatsvc/IC3 直连不可行：令牌能铸（`authz` 200 + skypeToken），但 `chatsvcagg` 五种头风格
  全 401；service worker 的请求 CDP 也抓不到（`new_cdp_session(worker)` 不支持，
  `Target.sendMessageToTarget` 已废弃）。

### 实测

- 单测 **1400+**（含 `tests/test_kb.py` 21、`tests/test_teams_logic.py` 40、
  `tests/test_teams_bot.py` 18）；不变量待跑。
- 真机：`api/idb-watch` 返回 50 个最近会话（27 个私聊），扫描周期 0 错误。

## [11.0.0-preview.45] - 2026-11-02

### Added

- **Teams 网页版应答机器人 + 本地知识库**（用户："最近做了个插件用来做 Teams web 版的机器人……
  失败了，估计得你来重新设计；有人单对单聊天并 @我 时用 Gantry 的 API 回复，并优先使用本地知识库"）。
  - `gantry/kb.py`：`data/kb/**/*.md` → SQLite **FTS5 trigram** 索引（中文整句可搜，
    默认 unicode61 不切中文），按 `#`/`##` 标题切块、带出处；增量重建（mtime+size）；
    打分改成 **0.75×查询词覆盖率 + 0.25×bm25 名次**（旧算法所有命中都接近 1.0，阈值失效）。
  - `gantry/teams.py`：Teams 网页自动化（`teams_open` / `list_chats` / `open_chat` /
    `recent_messages` / `send_message` / `probe`），复用桥接浏览器的 SSO；选择器多级兜底。
  - `gantry/teams_logic.py`：纯逻辑（@ 识别含全角、问题抽取、去重键、KB 答案拼装、
    `should_answer` 带"为什么没回"的原因）。
  - `plugins/teams-bot/`：轮询循环 + 工具（`teams_status`/`teams_reply`/`teams_probe`/
    `kb_search`/`kb_reindex`）+ 接口（status/run/reply/probe/reindex/settings）+ 面板；
    **默认 `enabled=false` + `dry_run=true`**，先在 `status.last_cycle.would[]` 里看"本来会回什么"。
  - `scripts/kb.py`：`sample` / `index` / `search` / `stats`；样例文件与说明自动写入 `data/kb/`。
  - `docs/TEAMS-BOT.md` + `plugins/teams-bot/README.md`（含校准流程与已知边界）。

### Fixed

- **Teams 就绪判定**：`/v2` 的 app shell 早就渲染、但聊天区可能还停在
  "We're setting things up for you…"，旧判定把这种状态当 ready → 抓不到会话/输入框。
  现在必须等到**会话项或输入框真的出现**，并在初始化期间打日志说明在等什么。
- **选择器按真机校准**（2026-11-02 实测，teams.cloud.microsoft/v2 已登录）：
  会话项 `chat-pane-item`、消息 `chat-pane-message`、作者 `message-author-name`（同级元素，
  按文档顺序配对）、输入框 `ckeditor`、发送按钮 `sendMessageCommands-send`（优先点按钮，
  比 Enter 稳）。`probe()` 增加 DOM 统计（data-tid/role/列表容器/iframe），
  不再 dump 整页 HTML（会被截断）。

### 实测

- Teams 网页在自动化浏览器里**已登录可用**（标题 `(3) Chat | …`）：会话 10 个、消息 6 条、
  输入框与发送按钮都能定位；`/v1/plugins/teams-bot/api/status` 返回知识库 2 篇 / 4 块 / FTS=true。
- 知识库：`python scripts/kb.py search "VPN 连不上"` → 命中《VPN 连不上怎么办 › 排查步骤》
  得分 1.000，症状段 0.755；无关 query（"火星怎么去"）返回空。
- 单测 **1375**（skipped=1；新增 `tests/test_kb.py` 21 条、`tests/test_teams_logic.py` 23 条、
  `tests/test_teams_bot.py` 14 条）；不变量待跑。


## [11.0.0-preview.44] - 2026-11-02

### Fixed

- **"8B 连搜新闻都做不到"其实是 web_search 工具在返回 0 结果**（用户带着截图反馈）。
  实测（同一台机器、同一个 query）：
  - `html.duckduckgo.com` **间歇性返回 0 结果**（反爬，有时直接 202）：同一句
    "small language model news" 前一次 3 条、后一次 0 条；
  - `lite.duckduckgo.com` 一律 **HTTP 202 空页**（解析不到任何结果块）；
  - Bing（HTML 与 `format=rss` 都试了）**相关性不可用**：`small language model news`
    → 《Small》期刊、Smallpdf；`今天 AI 新闻` → FedEx 物流 —— 所以**没有**拿它当兜底；
  - **Google News RSS 正常**：100 条、相关性对得上（"Small language models in medicine - Nature"）。
  修法（`plugins/web-search/plugin.py`）：
  - DDG 空结果**重试一次**（限流是间歇的，隔 1.2 秒往往就有）；
  - 仍为空则**回退 `gnews`**（Google News RSS，免 key，中英文 query 按语言选 hl/gl/ceid）；
  - `auto` 后端顺序变成：BRAVE/TAVILY/SERPER（有 key）→ ddg → gnews；
  - `web_search` 返回的 `errors` 里保留每次尝试的原因，模型能据此换策略而不是瞎猜。

### 实测

- 服务里 `GET /v1/plugins/web-search/api/search` 三个 query（含"今天 AI 新闻"）**全部有结果**
  （`backend=gnews, count=3`）。
- 用用户原话重新问 8B（`fflm/deepseek-r1-0528:8b`，Agent 模式）：209 秒、**2 步**，
  两次 `web_search` 都成功，最终答案是**真实新闻标题+来源**（CNN / Washington Post / Engadget），
  不再是"截至2024年7月"的凭记忆编造。
- 另外确认：**普通对话模式本来就没有工具**（`/v1/chat/completions` 不加载工具），
  所以那个模式永远不会联网——这不是模型的问题，要看新闻得用 Agent 模式（或 Copilot 网页模型）。
- 单测 **1317**（skipped=1，新增 `tests/test_web_search_plugin.py` 7 条：DDG 解析、gnews 解析、
  回退顺序、ddg 重试成功、全空时带 errors）；不变量 25 条全绿。

## [11.0.0-preview.43] - 2026-11-02

### Fixed

- **`hang.txt` 会无限增长**（我自己上一个版本带进来的）：`GANTRY_FAULTHANDLER=1` 用
  `faulthandler.dump_traceback_later` + **append**，而重启会**继承环境变量**，于是每个进程
  每 60 秒往同一个文件追加一份全线程栈（实测每小时约 600KB，且混着好几天前的会话）。
  - 改为**每次启动覆盖写**（`w`），一份文件只对应这一次进程；
  - 自己起守护线程每 60 秒 `dump_traceback(file=handle, all_threads=True)`；
  - 文件超过 2MB 就从头截断——只保留最近一次卡住的栈。

### 实测

- 开关默认关闭：干净重启后 `data/logs/hang.txt` **不会**生成（已验证）；
  需要排障时 `GANTRY_FAULTHANDLER=1 python start.py` 才会开。
- 单测 **1310**（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.42] - 2026-11-02

### Added

- **接入 FastFlowLM（NPU 本地推理，DeepSeek-R1 8B）**（用户："再加一个
  http://127.0.0.1:52625/v1 FastFlowLM 的，里面有 deepseek 8B 的，试下"）。
  `config.json` / `config.example.json` 新增 `providers.fflm`：`base_url` 指向 52625 的
  OpenAI 兼容层，`models: ["deepseek-r1-0528:8b", "deepseek-r1:8b"]`，`json_mode: json_object`
  （实测支持），`max_tokens: 4096`、`timeout_seconds: 600`（R1 会先思考）。三语 README 补说明。
  注意：**服务不会自己起来**，需要 `flm serve deepseek-r1-0528:8b`（默认端口 52625）；
  本次已用它起在后台（日志 `%TEMP%\fflm-out.log`）。

### Fixed

- **进程"卡死"没有任何痕迹**：`GANTRY_FAULTHANDLER=1` 时每 60 秒把所有线程栈写进
  `data/logs/hang.txt`。2026-11-02 重启后进程停在 uvicorn 的 "Waiting for application startup"，
  后台模式没有 stderr、`crash.log` 也是空的（excepthook 只在异常时触发），只能干瞪眼。

### 实测

- FastFlowLM 两个 8B 都能用（NPU，约 10 tok/s）：`deepseek-r1-0528:8b` 单轮 **25.5s**、
  `deepseek-r1:8b` 首轮 94s（含 15s 加载）；两者都**同时返回 reasoning 和 content**
  （R1 是思考型但不吞正文），`response_format: json_object` 也支持（25s 返回合法 JSON）。
- 经 Gantry 跑：`/v1/chat/completions` → 27s，`1+1 等于 2。`；
  **Agent 任务**（列出工作区文件并汇报）→ **70s、1 步完成**，`list_directory` 参数正确、
  用的是真实列表（总条目 10 对得上；把"6 目录 / 4 文件"数成了 7/3，小模型的算术抖动）。
  对比 Ollama 那个 4B：8 步连挂、还编造文件名——**8B 在 NPU 上明显可用**。
- `GET /v1/models` 出现 `fflm/deepseek-r1-0528:8b`、`fflm/deepseek-r1:8b`（available）。
- 单测 **1310**（skipped=1）OK（test_tray 26 条，+2 条卡死监控）；不变量 25 条全绿。

## [11.0.0-preview.41] - 2026-11-01

### Added

- **本地 Ollama 作为提供商接好了**（用户："本地有 ollama，帮忙看看配置一下做下测试"）。
  `config.json` 新增：

  ```json
  "providers": {
    "ollama": {
      "base_url": "http://127.0.0.1:11434/v1",
      "api_key": "ollama",
      "models": ["fredrezones55/Qwen3.5-Uncensored-HauhauCS-Aggressive:4b"],
      "json_mode": "json_object",
      "max_tokens": 4096,
      "timeout_seconds": 600
    }
  }
  ```
  模型 ID 是 `ollama/<模型名>`；`config.example.json` 与三语 README 都补了 Ollama 段。

### Fixed

- **OpenAI 协议从来没发过 `max_tokens`**（只有 anthropic 分支用）：思考型模型（Qwen3.5 等）
  会把服务端默认预算全烧在 reasoning 上，`content` 直接为空。本地 Ollama 实测：
  默认预算下只回 reasoning；**配 `max_tokens: 4096` 后正常出正文**。
  现在 OpenAI 请求体按配置带 `max_tokens`，显式写 `0` 表示**不发**该字段
  （有些网关不认它）；`max_tokens` 缺省 8192。
- **只拿到 reasoning 时报错可行动**：以前 `content` 为空就直接返回空字符串，agent 侧只看到
  "模型不返回动作"。现在报"只返回了思考过程（reasoning），正文为空……把
  providers.<名字>.max_tokens 调大（当前 N）"，`reasoning` / `reasoning_content` 两种字段名都认。

### 实测

- `GET /v1/models` 出现 `ollama/fredrezones55/Qwen3.5-…:4b`（`available: true`）；
  `python scripts/provider_selftest.py --provider ollama` → 经代理/直连都 HTTP 200（0.4–0.6s）。
- `POST /v1/chat/completions`（经本服务）→ 25 秒，`1+1 等于 2。`，正文正常。
- **Agent 工具循环这个 4B 思考模型撑不住**（如实记录）：8 步连续失败——自己编
  `recursive`/`max_depth`/`extensions` 参数、两次空工具名、甚至直接编造 final 结果不调用工具。
  单轮 20–60 秒。建议 Agent 任务换 8B 以上的指令模型（`qwen3:8b` / `llama3.1:8b`）。
- 单测 **1308**（skipped=1）OK（新增 `tests/test_provider_max_tokens.py` 7 条）；不变量 25 条全绿。

## [11.0.0-preview.40] - 2026-11-01

### Fixed

- **「重启服务」之后服务再也没起来，而且没有任何日志**（2026-11-01 21:36 实测）：
  点了重启，uvicorn 优雅停机、生命周期 `exec` 重启都跑了，然后……什么都没有。日志最后一行
  是"正在重启服务"，新进程连"日志已就绪"都没打，只能靠猜。
  - `start.py` 的 UTF-8 设置改成 `_utf8_stdio()`：**逐流判空 + 兜异常**
    （`pythonw` / `DETACHED_PROCESS` 下 `sys.stdout` / `sys.stderr` 可能不可用，
    直接 `.reconfigure()` 会在"日志还没配好"的时刻抛异常，进程静默死掉）。
  - 新增**崩溃日志** `data/logs/crash.log`：`sys.excepthook` + `main()` 启动期兜底，
    把 traceback 写文件——后台模式没有 stderr，不留痕就等于没发生。
  - 托盘「重启服务」不再"睡 1.5 秒就说已重启"：现在**等 `/health` 真的 ok**（最多 60 秒），
    等不到就弹"60 秒内没起来，看 data/logs/crash.log"并在日志里警告。

### Added

- `scripts/smoke_restart_action.py`：直接调用 `Tray._a_restart()` 走**菜单点下去**的同一条
  代码路径（打桩 balloon/http），断言"先 POST /v1/restart → 再轮询 /health → 才报成功"。
- `tests/test_tray.py` 增 4 条：崩溃日志真的写出来（含 traceback）、托盘重启必须等 /health、
  三语言都有超时提示且指向 crash.log、start.py 装了 excepthook。

### 实测

- 托盘重启动作端到端：耗时 4.6 秒、3 次 HTTP（restart → health → …）、气泡
  "正在重启服务…" → "服务已重启"，`data/logs/crash.log` 未产生。
- 后台启动：`pythonw3.13.exe start.py`（无控制台）跑 preview.39/40，`/health` 200。
- 单测 **1301**（skipped=1）OK；不变量 25 条全绿。
## [11.0.0-preview.39] - 2026-10-30

### Fixed

- **「重启服务」会弹出控制台窗口**：`lifecycle.exec_restart()` 的 `Popen` 加
  `creationflags=8`（`DETACHED_PROCESS`），重启出来的进程不再附着/新建控制台。
- **重启后可能不再是托盘模式**：重启改为显式传 `GANTRY_TRAY_CHILD=1`
  + `stdin/stdout/stderr=DEVNULL` + `close_fds=True`（原来靠环境继承，从别处启动时会丢）。
- **托盘状态不刷新**：新增状态刷新线程（每 8 秒 `NIM_MODIFY` 更新 tooltip），
  不用等下次点开菜单才看到"运行中 / 未响应"和版本号。

## [11.0.0-preview.38] - 2026-10-30

### Fixed

- **Windows 控制台中文日志乱码**：`start.py` 在 `main()` 开头把 stdout/stderr 强制成 UTF-8
  （代码页不是 65001 时，`日志已就绪…`、`托盘已就绪…` 这类中文会打成乱码）。
  注：这行后来补了判空（见 preview.40），否则 pythonw 下 `sys.stdout` 不存在时会直接崩。

## [11.0.0-preview.37] - 2026-10-29

### Changed

- **浏览器标签页的 favicon 也换成 G**（用户："浏览器里的 favicon 呢"）。原来 index.html 里是
  一段**内联 SVG**（桥形线条），跟托盘那个 G 图标不一致，而且内联 data URI 没法被托盘复用。
  - 图标**单源**到 `webui/favicon.ico`（8 个尺寸）：`index.html` 用
    `<link rel="icon" href="favicon.ico">`，托盘 `start.py` 也从同一文件读；
    `assets/gantry.ico` 删除（原来那份副本会和 favicon 漂移）。
  - `/ui/{asset}` 白名单补 `favicon.ico`（不变量"页面引用 ⊂ 白名单"会核对）。
  - `scripts/make_tray_icon.py` 输出改到 `webui/favicon.ico`，预览图仍在
    `data/ui-audit/tray-icon.png`。

### 实测

- `GET /ui/favicon.ico` → 200、`image/x-icon`、20 002 字节；
  浏览器里 `link[rel=icon]` 的绝对地址是 `http://127.0.0.1:8765/ui/favicon.ico`。
- 探针 `scripts/smoke_favicon.py` 断言"页面 favicon 与托盘同一份"；
  单测 **1297**（skipped=1）OK；不变量 25 条全绿。
- 注意：浏览器会缓存 favicon，第一次可能需要 Ctrl+F5 才会看到新的 G。

## [11.0.0-preview.36] - 2026-10-29

### Changed

- **托盘图标改成字母 G**（用户："图片用 G 字吧"）：品牌绿圆角底 + 白色粗体 G，与界面空状态
  那个「G」品牌块一致；原来那版是"桥拱"线条。
  - `scripts/make_tray_icon.py` 改用 Segoe UI Bold（缺失时退 Arial Bold / PIL 默认字体），
    并按实际包围盒居中（不同字体 G 的左右留白不同，用 anchor 会偏）。
  - 16/20/24 小尺寸字号给到 0.92 倍（大尺寸 0.78），避免小图标里 G 又小又细；缩放后仍是 8 个尺寸。
  - 脚本顺带渲染预览图 `data/ui-audit/tray-icon.png`（各尺寸 1:1 + 16px 放大 6 倍），
    换图标时肉眼可核对，不用等它出现在托盘里。

### 实测

- `assets/gantry.ico` 重新生成（20 002 字节 / 8 个尺寸），预览图确认 16px 下仍可辨认。
- 重启后 `python start.py --tray-selftest` 通过（Shell_NotifyIconGetRect 返回图标矩形）。
- 单测 1296（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.35] - 2026-10-29

### Added

- **系统托盘 + 后台运行**（用户："做一个系统托盘图标吧，还是一个启动器 start.py，
  但可以不跑 login.py 就启动托盘，通过托盘菜单启动自动化浏览器；start.py 之后能关掉终端，
  日志也要留档"）。
  - `python start.py` 现在**默认派生后台进程**（同目录 `pythonw.exe` +
    `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`），终端随即可以关掉；前台仍是
    `--foreground`。子进程带 `GANTRY_TRAY_CHILD=1`，所以「重启服务」之后依然是托盘模式。
  - `gantry/tray.py`：**纯 ctypes**（user32/shell32）实现托盘——消息窗 + `Shell_NotifyIconW` +
    `TrackPopupMenu`，不引入 pystray/Pillow 之类依赖。菜单每次弹出重建（状态是活的）。
  - 菜单（**三语言**：简体 / 繁體 / English，用户："菜单要多语言，不要那么宽"）：
    打开控制台 · 启动自动化浏览器 · 打开日志 · 打开数据目录 · 重启服务 · 语言 ▸ ·
    退出托盘 · 停止服务并退出。状态/版本/端口移到**图标 tooltip**，菜单首行只留版本号，
    标签去掉括号注解（宽度问题就是那次反馈）。
  - 托盘**不依赖** `scripts/login.py`：菜单里的「启动自动化浏览器」直接调
    `POST /v1/agent/browser`（按需拉起，登录态沿用 `EdgeProfile`）。
  - 语言来源：`config.json` 的 `ui.language` > `GANTRY_LANG` > Windows 界面语言，默认中文；
    菜单里切换会写回 `config.json`，下次启动保持。
- `gantry/logcfg.py`：日志落盘 `data/logs/gantry.log`（5 MB × 7 滚动，`GANTRY_LOG_DIR` /
  `GANTRY_LOG_LEVEL` 可改）。后台模式没有终端，uvicorn 启动/访问日志与托盘动作
  （"托盘图标已添加""托盘：启动自动化浏览器"）都进这个文件。
- `assets/gantry.ico`（8 个尺寸）+ `scripts/make_tray_icon.py`；`--print-menu` /
  `--tray-selftest` / `--lang` 三个开关（后两个便于排障与自动化验证）。
- `tests/test_tray.py`（19 条）：菜单动作都有实现、**标签必须短**、三语言词条齐全、
  语言切换写回 config.json、`--print-menu` 可用、派生命令用 pythonw + `--tray-child`、
  uvicorn 不在模块顶层 import、日志落盘且 handler 不重复。

### Changed

- `start.py`：`uvicorn` 改成函数内 import（`--help` / `--print-menu` / `--tray-selftest`
  不再被它拖住）；日志统一由 `logcfg` 配置（`log_config=None`，否则每条日志打两遍）；
  单实例：启动前先探 `/health`，已在运行就只打印地址。
- `config.example.json` 增加 `ui.language`；`AGENT.md` 与三语 README 的快速开始补一句
  "默认后台运行"；新增 `docs/TRAY.md`。

### 实测

- `python start.py` → 4.6s 返回，服务在 **pythonw3.13**（无控制台）里跑，
  `GET /health` 200；日志里有「日志已就绪（控制台=False）」「托盘图标已添加（pid=…）」
  「Uvicorn running on …」；再执行一次只提示"服务已在运行"，不重复派生。
- 托盘图标注册用 `Shell_NotifyIconGetRect` 验证（返回 40×40 矩形），不靠肉眼看折叠区。
- `POST /v1/restart` 后进程仍是 pythonw、托盘自动重建（pid 变化，日志有新的一条"托盘图标已添加"）。
- 「启动自动化浏览器」实测：调用返回 `browser_state=ready, copilot_ready=true`。
- 单测 **1296**（skipped=1）OK；不变量 25 条全绿。

## [11.0.0-preview.34] - 2026-10-29

### Fixed

- **「继续对话」输入框里打 `/` 没有菜单**（用户："我按了 / 没出来提示，不知道工具有没有生效"）。
  斜杠菜单原本只挂在「新任务」框上：`#task` 有 `data-act-input/key` 且 `slash.js` 里
  把 `#task` / `#slashMenu` 写死了，跟进框既没有这两个属性、也没有自己的菜单容器，
  所以按 `/` 什么都不会出现（按 Enter 还会把 `/xxx` 当普通消息发出去）。
  - `slash.js` 改成"成对目标"模型（`SLASH_PAIRS`：`task→#slashMenu`、
    `followup→#slashMenuFollow`）；`slashUpdate/slashPick/slashKey` 通过 `_pair()` 取当前输入框与菜单，
    `slashOpen()` 看所有菜单，`slashRun()`/Esc 关掉全部；
  - `index.html` 给 `#followup` 加 `data-act-input="slashFollowInput"` /
    `data-act-key="slashFollowKey"`，并新增 `#slashMenuFollow`（`.atmenu.slashmenu`，绝对定位在输入框上方）；
  - `settings.js` 的 ACTIONS 增加两个入口，并让 `taskInput` 也把元素传下去（否则菜单分不清是谁弹的）；
  - `composer.js` 的 Enter 处理**先问斜杠菜单**：菜单开着时 Enter 是"补全/执行命令"，
    不再把 `/xxx` 发出去。

### Added

- `tests/test_slash_followup.py`（7 条）：跟进框属性与菜单容器、ACTIONS 注册、
  `_pair()` 取代写死的 `#task`、`slashOpen`/`slashRun` 覆盖全部菜单、Enter 先问菜单再发送。
- 浏览器冒烟 `scripts/smoke_slash.py`：跟进框打 `/` 弹出 9 条命令 → ↓ 换选中项 →
  Enter 补全为 `/fanout ` 且菜单收起；新任务框的菜单未受影响。

### 实测

- 单测 **1277**（skipped=1）OK；不变量 25 条全绿；`cross_module_refs.py` OK；
  截图 `data/ui-audit/slash-followup.png`。

## [11.0.0-preview.33] - 2026-10-29

### Fixed

- **未选会话时出现两个输入框**（用户截图："两个输入框会让人感到困惑"）：中间是「新任务」框、
  底部还挂着「继续对话」框，看不出该用哪个。
  根因是启动时 `syncComposer()` **没执行**：它在 `settings.js` 的**模块顶层**以
  `try{syncComposer()}catch(e){}` 调用，而 `syncComposer` 只由 `main.js` 绑到 window，
  绑定发生在所有 import 之后 → import 阶段 `ReferenceError`，被空 catch 吞掉；
  空状态又是"默认可见"、不产生 `style` 变化，`MutationObserver` 也不触发，于是没人算。
  - 启动同步挪到 `main.js` 的 `bootInit()`（直接用 import 的函数，不靠 window）；
  - 删掉 `settings.js` 里那条必然抛错的裸调用（留注释说明为什么不能放这里）；
  - 观察器从只看 `style` 扩到 `style` / `hidden` / `class`（class 切换时也不会漏）。

### Added

- **跨模块检查新增 E 类**（`scripts/cross_module_refs.py`，也并入不变量第 25 条）：
  "模块顶层调用只靠 window 绑定的函数"——这类调用在 import 阶段必然 ReferenceError，
  若外面裹了 try/catch 就是**静默不执行**。判定收窄到"语句开头 + 非箭头回调 + 本模块没声明/没 import"，
  避免把 `addEventListener(...,()=>f())`、`setInterval(f,1500)` 误报。
  同一类坑已经咬过两次（`mountTeamCard`、这次的 `syncComposer`）。
- `tests/test_esm_migration.py` 增 6 条回归：真违规要抓到（`try{f()}catch(e){}`、裸语句），
  回调/引用/函数体/已 import 都不许误报。
- 浏览器冒烟 `scripts/smoke_composer.py`：按"**真正可见的 textarea 数量**"判定——
  空状态 1 个、选中会话 1 个、点「＋ 新任务」回到空状态又 1 个。

### 实测

- 单测 1270（skipped=1）OK；不变量 25 条全绿；`cross_module_refs.py` OK；
  浏览器三种状态可见输入框数均为 1、无 JS 异常；截图 `data/ui-audit/composer-empty.png` /
  `composer-session.png`。

## [11.0.0-preview.32] - 2026-10-29

### Fixed

- **拉取远端之后侧栏版本号与页签标题消失**（远端 `daf8b49 feat: update ui route` 把
  `GET /` 改成 302 → `/ui/`，而前端 `loadVersion()` 仍把根路径当 JSON 用：
  `fetch('/')` 跟随后拿到 HTML → `r.json()` 抛错 → 被 catch 吞掉 → 版本号静默不显示）。
  - `/health` 现在带 `name` + `version`（界面本来就每 8 秒轮询它，等于零额外请求）；
  - 前端新增 `fetchMeta()`：先 `/health`，再兜底 `/`（老部署根路径仍是 JSON），
    并按 `content-type` 跳过被重定向成 HTML 的响应；
  - `root()` 里 `return RedirectResponse(...)` 之后那段**不可达**的 JSON dict 已删
    （留着会让人误以为根路径还是 API）；显式 `status_code=302`。
  - 探针 `scripts/smoke_version.py`：页签 `Gantry - v11.0.0-preview.32`、侧栏 chip 可见。

### 说明

- 本次一并追平远端 6 个提交（`daf8b49`…`9804a5d`：UI 根路由、tool plugin 加载器与内置插件、
  Windows 短路径 root 的 `relative_to` 修复、两个新 skill、`webui/js/composer.js` 追加）。
  拉取后本地跑通：单测 **1265**（skipped=1）OK、不变量 25 条全绿、JS 语法全通过。
- 远端 `preview.28`–`preview.31` 当时只 bump 了版本号、CHANGELOG 是**空占位**；
  现已按这几个提交的实际 diff 补齐（28 的根路由/下载重试/斜杠命令、29 的纯版本推进、
  30 的插件机制与短路径修复、31 的发布 skill）。

## [11.0.0-preview.31] - 2026-10-29

### Added

- 新 skill `skills/self-upgrade-github-release/SKILL.md`：自升级完成后按**实际改动**总结提交信息，
  再走 `bump_version.py` → commit → tag → push GitHub 的完整流程（前置条件：`self_validate`
  与 `self_unittest` 都 passed=true，并先看 `git status --short` / `git diff`）。

## [11.0.0-preview.30] - 2026-10-29

### Added

- **工具插件机制**：`gantry/tool_plugin_loader.py` —— 扫描 `gantry/tool_plugins/` 下的模块，
  调它的 `register(registry)` 注册工具；支持 `config.json` 的 `tool_plugins.disabled` 禁用列表、
  重名检测与失败隔离（一个插件坏了不影响其它）。
- 4 个内置插件工具：`batch_analyze`（批量读取/分析多个文件）、`command_capabilities`
  （探测环境里命令的能力：类型/属性/路径）、`workspace_inspect`（查看工作区结构与大小）、以及
  `tool_plugins/init.py`（包声明）。
- `plugins/coding-harness-tools/`：外部插件示例（`plugin.json` + `plugin.py`），
  配 `skills/gantry-tool-plugin/SKILL.md` 说明怎么写插件。
- 新工具的中英文描述与参数提示（`tool_i18n.py`）。

### Fixed

- **Windows 短路径 root**（如 `ADMINI~1`）：`atrefs._resolve()` 与 `tools_self._self_rel()`
  用短路径 root 对已 resolve 成长路径的 candidate 做 `relative_to`，直接 `ValueError`，
  表现是 **@ 引用与自升级搜索全空**。两处改成先 `resolve()` 再比较。
- `copilot_file` 只允许写**当前工作区**（显式 `relative_to` 校验，越界报"只能写入当前工作区"）。
- 自升级白名单补 `plugins/`（`tools.py` 与 `tools_self.py` 双处），否则插件目录始终改不了。
- `bridge.py` 多余空行。

## [11.0.0-preview.29] - 2026-10-29

### Changed

- 仅版本号推进（该提交只改了 `CHANGELOG` / `README` / `version.py` / `pyproject.toml`，无功能改动）。
- 同一版本窗口内还落了 Windows 短路径 root 的修复（`8392931`），随 preview.30 一起发布，
  详见下一节。

## [11.0.0-preview.28] - 2026-10-29

### Added

- `copilot_file` 取下载结果时**重试等待**：模型刚生成完就去点下载，目录里往往还是空的；
  现在 30 秒内每 0.5 秒轮询一次，拿到就返回（原来的行为是一次没找到就直接放弃）。
- 「继续对话」输入框也支持**斜杠命令**：以 `/` 开头且 `slashRun` 认领的命令不再当普通消息发出去。

### Changed

- **`GET /` 改为 302 → `/ui/`**：访问 `http://127.0.0.1:8765/` 直接进控制台
  （原来返回 `{name, version, docs, models, agent}` 元信息）。
- `AGENT.md` 补三条执行约定：保持实现简单（不为单次用途加抽象）、改动可追溯
  （只改与目标直接相关的部分，无关问题只报告）、以可检查的成功条件驱动执行。

## [11.0.0-preview.27] - 2026-10-29

### Fixed

- **「保存」误报"没有改动"**（用户实测："我对 teamscope 进行了设置然后点保存，提示没有更新，
  实际上是生效的"）。根因：`saveSettings()` 只用 `collectSettings()` 收 `[data-cfg]`
  （**服务端 config** 字段）来算"有没有改"，而动画控件是 `data-anim`（本地 localStorage），
  于是 values 恒为空 → 直接命中 `toast('没有改动')` 早退分支。主题/语言同理（它们一直中招，
  只是变化即时可见、没人注意到提示）。
  - 打开设置时给**本地设置**拍快照（主题 / 语言 / 动画参数），保存时对比；
  - 只有本地改动 → `本地设置已保存（N 项）`；服务端 + 本地都改 → `设置已保存（服务端 N 项 · 本地 M 项）`；
    两边都没改才说 `没有改动`；
  - 保存后刷新快照，所以"连着点两次保存"第二次会如实说没有改动。
  - 复现探针 `scripts/smoke_save_toast.py`：只改动画 → "本地设置已保存（1 项）"；
    再点一次 → "没有改动"；关掉重开直接点 → "没有改动"。

### 实测

- 单测 **1265**（skipped=1）OK（新增 4 条 SaveToastTests）；不变量 25 条全绿；
  探针三种情形的 toast 文本均正确，无 JS 异常。

## [11.0.0-preview.26] - 2026-10-29

### Added

- **设置 → 外观 → 动画：示波器与输入框呼吸都能调了**（用户："把 teamscope 能调的参数弄出来放设置里，
  还有输入框边框呼吸的参数"）。这正是 task-mvs4un2x 想做但半途而废的功能——那次只往
  `settings.js` 塞了 4 行「读元素」的代码，元素从没加进界面，ui_refs 不变量当场报红。

  | 分组 | 控件 | 默认值 = 调参前的原始观感 |
  | --- | --- | --- |
  | 示波器 | 开关（沿用老的 `gantry-teamscope-heartbeat`） | 开 |
  | | 振幅 40%–160% | 100%（半高的 80%） |
  | | 线宽 0.6–4 px | 1.8 px |
  | | 透明度 15%–100% | 100% |
  | | 速度 0.3–2.5× | 1.0×（波形横向流动） |
  | 输入框呼吸 | 开关 | 开 |
  | | 时长 0.6–4.0 s | 1.8 s |
  | | 强度 0–100% | 100%（边框混 88% 强调色 + 18px 光晕） |

- 新模块 `webui/js/animprefs.js`：参数读写/区间钳制（手改 localStorage 写坏成 `abc` 也不会让动画
  变 NaN 静默消失）、把呼吸参数写成 `<html>` 上的 CSS 变量、改完派发 `gantry-animprefs` 事件让
  示波器丢缓存。**拖动即时生效**（不用等「保存」），保存按钮也会落库；刷新后保持。
- 首屏启动就 `applyAnimPrefs()`，CSS 变量缺失时关键帧自带 fallback（默认值 = 旧观感），
  所以「不打开设置页」时视觉与以前完全一致。

### Changed

- `css/composer.css` 的 `composer-breathe` 关键帧参数化：`--breathe-dur / -hi / -ring / -ring-a /
  -glow / -glow-a`，并新增 `:root[data-breathe="off"]` 关掉动画；`prefers-reduced-motion` 兜底保留。
- 使用手册「界面」一节（中英）补一条说明。

### 实测

- 浏览器探针实测（不是只看代码）：振幅 0.4→1.6 时示波器墨迹跨度 4px→14px、线宽 1.8→4 时
  厚度 3→5px、透明度 1→0.25 时峰值 alpha 255→64；拖滑杆后 localStorage 与 `--breathe-dur/
  --breathe-hi/--breathe-glow` 同步变化；关呼吸后 `data-breathe="off"`。
- 英文/繁中模式下设置页这 6 行**零中文残留**；截图 `data/ui-audit/anim-settings.png`。
- 单测 **1261**（skipped=1）OK（新增 `tests/test_anim_settings.py` 15 条）；不变量 25 条全绿；
  `ui_layout_audit --stress`（7 视口）/ `ui_contrast_audit` / `ui_i18n_audit`（三语言 446 条）通过。

## [11.0.0-preview.25] - 2026-10-29

### Fixed

- **自升级任务 task-mvs4un2x 的失败复盘 → 四类根因逐个修掉**（用户："检查一下失败原因，修正"）。
  那次任务 120 步里 44 步是异常，逐条看过原始 raw_tail 后，**没有一条**是"模型不会用工具"：

  | 现象 | 次数 | 真因 | 修法 |
  | --- | --- | --- | --- |
  | `self_read_text() got an unexpected keyword argument 'line_start'` | 1 | 普通 `read_text` 支持按行读，`self_read_text` 只支持字符偏移 | 加 `line_start`/`line_count`（返回带行号 + `total_lines`），`offset`/`limit` 保留 |
  | `invalid_action`（动作解析失败） | 10 | ① 字符串值里未转义的 `"`（`type==="bool"`）；② 正则里的 `\(` 不是合法 JSON 转义 | 新增 `_escape_inner_quotes`（按"结束引号后必须紧跟 `,}]:`"判定），`_repair_json_escapes` 扩到所有非法转义（`\(` `\[` `\d` `\s` `\w` `\|` …，补一个反斜杠以保留正则语义） |
  | `self_replace_text 未找到要替换的原文本` | 6 | 模型凭记忆拼 `old`，差一个空格就失败，只能反复重读 | 宽容匹配（CRLF/LF、行首尾空白差异）；失败时用 difflib 返回**最接近的真实行 + 行号**，让它一步改对 |
  | `list_directory('..') [sandbox: FS_SANDBOX_ESCAPE]` | 1 | 自升级模式下普通文件工具仍锁在工作区，报错没告诉它该用哪套工具 | 沙箱报错补第 ④ 条：服务安装目录请用 `self_*` 工具（并列出具体名字） |

- **启动时的"待生效自升级"自检门**（这次事故的直接来源）：任务被取消后，半成品改动留在盘上
  ——`webui/js/settings.js` 多了 4 行读 `setTeamScopeAmp`/`setTeamScopeLine`/`setTeamScopeOpacity`/
  `setComposerBreatheDur`，而这 4 个元素从没被加进设置页；旧逻辑在启动时只把
  `data/.self_upgrade_pending` 改名 `.applied` 并打印"已加载上一轮自升级改动"，
  **等于把坏代码无声当成成功**，之后"前端 id 引用完整性"不变量直接报红。
  现在 `mark_self_upgrade_applied()` 先跑确定性自检（文件存在性 / `.py` 编译 / ui_refs），
  不过就**保留**待办标记并在 `GET /v1/agent/self/status` 的 `pending_invalid` 里说明原因。
  本次已把那份半成品改动回滚（`webui/js/settings.js` 复原）并把旧标记归档为
  `.self_upgrade_pending.abandoned`。

### Added

- `tests/test_self_upgrade_recovery.py`（12 条）：用该会话**真实的失败原文**做用例——
  未转义引号、正则反斜杠、合法 JSON 不被改坏、按行读、CRLF+缩进宽容替换、失败时给最近原文、
  沙箱提示只出现在自升级模式、待生效标记的自检门（语法错/文件缺失/元素 id 缺失都不许归档）。

### 实测

- 单测 **1246**（skipped=1）OK（新增 12 条）；不变量 25 条全绿（此前因半成品改动红了一条）。
- 两条真实样本现在都能解析：`type==="bool"` 与 `startScope\(|stopScope\(` 均恢复为可执行动作。

## [11.0.0-preview.24] - 2026-10-29

### Changed

- **样式按组件族重排**（用户：「动它」）：上一版把样式切成 11 段时，最后那段
  `refinements.css`（650 行）是历史上十几轮打磨累积下来的"杂物箱"。这次按组件摊开：
  - 新增 **`rows.css`（473 行）** —— 底部四条横条（待办 / 子代理 / 调用记录 / 状态）的行高、
    内边距、hover、展开区，以及底部块的滚动布局（226 条规则）；
  - 新增 **`changes.css`（46 行）** —— 任务完成卡片 + diff 浮层着色（28 条）；
  - 其余并入 `shell.css`（侧栏页脚/窄屏抽屉 16 条）、`chat.css`（正文可读性/文件卡片 25 条）、
    `dialogs.css`（toast 置顶 5 条）、`layout.css`（离屏跳过、对话区两栏定稿、示波器 5 条）、
    `composer.css`（发送/停止按钮、@ 弹层 11 条）；
  - `refinements.css` 删除，链接顺序仍把它原来的位置留给 rows/changes（层叠位置基本不变）。

### Added

- **`scripts/css_style_diff.py`：改 CSS 的"行为不变"探针**。记录 `/v1/**` 响应 → 用同一份数据
  驱动同一个页面 → 在 12 个场景（默认/会话/展开四行/设置/手册/插件/新任务 × 暗/浅 ×
  1400/900/480 宽）里**逐元素比对约 85 个布局与颜色属性**，并比对 DOM 结构。
  自检：不改 CSS 连跑两遍 = 0 差异；拆分与重排两步实测都是 **0 差异**（约 9800 条元素记录）。
- `docs/CSS-SPLIT.md` 补上重排后的 12 文件映射表、"改样式后必须做的事"（含探针命令）。

### Fixed

- **重排时差点踩中的层叠事故**：第一版搬运脚本用正则切块，撑不住嵌套的 `@media`，
  媒体查询外壳被吃掉 → 窄屏的"浮动齿轮"页脚规则跑到宽屏生效（页脚变成 34px 固定圆钮）。
  探针报出 540 处属性差异后改用"按大括号深度切顶层语句"，`@media`/`@keyframes` 整体搬运。

### 实测

- 计算样式对比：12 场景 **0 差异**（DOM 结构也一致）；
- 单测 **1234**（skipped=1）OK；不变量 25 条全绿；
- `ui_smoke` / `ui_layout_audit --stress`（7 视口，无溢出/重叠/裁剪）/ `ui_contrast_audit`（全达标）通过。

## [11.0.0-preview.23] - 2026-10-29

### Changed

- **样式也拆了：`webui/style.css`（2340 行 / 136 KB）→ `webui/css/*.css`（11 个文件）**。
  只按**原有章节边界**切段搬家，不重排、不合并选择器：11 个文件的正文按顺序拼接与原文件
  **逐字节相同**（136375 字符 / 2341 行，拆分脚本内部断言 + 独立复核），所以层叠零变化。

  | 文件 | 行 | 内容 |
  | --- | --- | --- |
  | tokens | 28 | 设计令牌（`var(--…)` 的唯一来源） |
  | shell | 267 | 侧栏 / 主区 / 任务条折叠 / KPI + 媒体查询 |
  | chat | 194 | Markdown、时间线、工具结果、diff/JSON、语法高亮 |
  | dialogs | 45 | 遮罩、确认框、删除结果、页面内通知 |
  | themes | 134 | 7 套主题令牌 + 原生控件跟随 |
  | overlays | 148 | 手册、横幅、浮层分车道、子代理面板、插件弹窗 |
  | composer | 363 | 新会话视图、工作区、斜杠、目标/待办条、插件面板 |
  | layout | 219 | 主列 flex、输入框浮动层、窄屏抽屉、会话行、步骤页高度 |
  | settings | 217 | 设置页与相关打磨 |
  | alerts | 76 | 中断提醒、限流遮罩 |
  | refinements | 650 | 累积的收尾打磨（完成卡片、侧栏页脚、toast、四条横条统一） |

- `index.html` 改为 11 个并列 `<link>`（**顺序 = 层叠顺序**），不用 CSS 的 import 指令
  （会串行阻塞首屏）；`/ui/{asset}` 白名单去掉 `style.css`，正则改为
  `(?:js|css)/<名字>.(?:js|css)`。

### Added

- `gantry/ui_src.py` 新增 `ui_css()` / `css_rule()`：测试统一从这里取样式
  （18 个原本直接读 `style.css` 的测试已切换，避免"改在 A 文件、断言在 B 文件"的假覆盖）。
- `tests/test_css_split.py`（8 条）：老文件已删除、11 个文件齐全且顺序与 `CSS_ORDER` 一致、
  `index.html` 全量引用、无 import 指令、每个文件带自己的头注释、令牌/主题/媒体查询仍在。
- `docs/CSS-SPLIT.md`：文件映射表 + 三条规则（顺序即层叠 / 不许 import / 新规则放对文件）。
- 不变量 #15 扩展：认识 `css/` 目录，并**反向**检查"css/ 下有页面未引用的死文件"。

### Fixed

- 使用手册与 skill 里的自助示例还写着 `webui/style.css`（文件已不存在）→ 改为
  `webui/css/tokens.css` 的 `.app`（侧栏宽度 318px 就在那儿）。

### 实测

- 单测 **1234**（skipped=1）OK；不变量 25 条全绿；
  `/ui/css/*.css` 11 个全部 200（2.3–42.3 KB），`/ui/style.css` 404（预期）。
- 浏览器：无 JS 异常、会话可打开；`ui_layout_audit --stress`（7 视口）无溢出/重叠/裁剪，
  `ui_contrast_audit` 全部达标，`ui_i18n_audit` 三语言 446 条无泄漏。

## [11.0.0-preview.22] - 2026-10-28

### Changed

- **app.js 拆完退役（ES Modules 迁移收尾）**：`webui/app.js`（原 5139 行单体）删除，
  业务逻辑全部在 `webui/js/*.js`（30 个 ES 模块），`js/main.js` 是唯一入口。
  `index.html` 现在只加载 `js/globals.js`（经典脚本，共享状态）+ `i18n.js` + 模块入口。
- **新增 `webui/js/globals.js`（跨模块共享状态）**：ES 模块顶层的 `var/let` 是**模块私有**，
  别的模块裸名访问会 ReferenceError（严格模式），而各处 `try/catch` 一吞，界面只表现为
  "空白/不刷新"。经典脚本顶层的 `var` 既挂 window 又在全局词法环境 → 模块可裸名读写，
  与拆分前语义一致。共收口 43 个状态 + 4 个被 `window.X` 误当全局用的私有变量。
- **常量/函数改走真正的模块边界**：分享常量（THEMES/SLASH_COMMANDS/MANUAL_HTML/
  SETTINGS_GROUPS/PHASE_LABEL/HL_LABEL…）改为 owner 模块 `export` + 消费方 `import`；
  函数仍 `export` 并在 `js/main.js` 绑 `window`（老调用点一行不改）。
- **`/ui/{asset}` 白名单**去掉 `app.js`（`js/<名字>.js` 正则已覆盖所有模块与 globals.js）。

### Added

- **跨模块共享检查进不变量（第 25 条）**：`scripts/cross_module_refs.py` 静态分析
  ① 共享状态是否只在 globals.js、② 跨模块函数是否 export+window 绑定、
  ③ 同名函数是否被两个模块各声明一份、④ `window.X` 是否指向模块私有变量；
  发现问题既是独立脚本（退出码 1）也直接进 `invariants.py`。
- 浏览器探针 `scripts/smoke_interact.py`：真的点开一个会话，断言时间线/工具记录/
  状态栏有内容且无 console 错误（只查 window 绑定查不出"列表空白"这类问题）。

### Fixed

- **会话列表整片空白**（迁移中途的真实回归）：`drawSessions` 抛 `ReferenceError`
  被 `refreshSessions` 的 `try/catch` 吞掉 —— `expandedSess/showSubagents/subagentCount`
  等状态在模块里各有一份私有声明。收口到 globals.js 后恢复（实测 21 个会话正常渲染）。
- **任务条点击空白处收起失效**（老 bug，迁移时暴露）：`header.topbar>.trow` 的 click
  处理器被绑了两次（原 app.js 里就有两份），切两次等于没切。删掉重复绑定。
- **视图缓存/权限档位两份状态**：`renderedSid/lastSessionObj/skipDetailOnce`（steps.js 私有，
  followload.js 却写 `window.*`）与 `sandboxMode`（themeboot.js 私有，settings.js 走
  `window.sandboxMode`）→ 收口到 globals.js，切回旧会话不再命中错缓存。
- `gantry/ui_refs.py` 改为扫描**全部**前端 JS（原只看 app.js）；`invariants.py` 里
  读 app.js 的四处（ACTIONS 表、手册中英对照、tr() 调用统计、子代理 parent 检查）
  同步改为读模块目录。
- 测试：`tests/test_esm_migration.py` 重写为**终态**回归（app.js 必须不存在、
  globals 必须在 i18n 之前、共享状态不许在模块里再声明、导出必须绑 window）；
  其余 30 个读 app.js 的测试改用 `gantry/ui_src.py` 的 `ui_js()/fn_body()`
  （按原 app.js 顺序拼接，避免"按函数名切段"切空）。

### 实测

- 单测 1226（skipped=1）OK；不变量 25 条全绿；`ui_smoke.py` /
  `ui_i18n_audit.py`（3 语言 449 条）/ `ui_layout_audit.py --stress`（7 视口）全部通过。
- 浏览器真交互：点开会话 `task-mvs4un2x` → 39 步、4 条消息、调用记录 62 行、状态条
  `completed · 步 39 · Token 19073`，无 console 错误（截图 `data/ui-audit/esm-final.png`）。

## [11.0.0-preview.21] - 2026-10-28

### Added

- **app.js 拆成 ES 模块（第 1 步）**（用户选了真模块化）：脚手架 webui/js/ —
  state.js（共享可变状态的家）、main.js（入口，把拆出去的全局函数绑到 window）、
  第一块 **scope.js**（示波器浮层，约 131 行、零跨块依赖，整体搬出）。
  - 增量策略：拆出去的块把老代码仍在调用的函数绑到 window，事件路径的调用点不用动；
  - 路由 /ui/{asset:path} 放行 js/<名字>.js（带穿越防护）；
  - 顺手修了两个积压：oot.js（localStorage 迁移）**从没被加载**（死代码）→ 现在
    index.html 里第一个加载；白名单不变量适配新路由形态（精确名单 + js/*.js 正则）。
  - 实测：浏览器里 startScope/stopScope/scopeFrame 都是 function、无 JS 错误、
    boot.js 迁移真的跑了；单测 1226（skipped=1）OK。

## [11.0.0-preview.20] - 2026-10-28

### Added

- **前端 id 引用完整性检查**（用户："加上"）：`gantry/ui_refs.py` —— 扫
  `webui/index.html` 与 `webui/app.js`，JS 里 `getElementById/querySelector` 引用的
  元素 id 必须在 index.html 的 `id=…` 或 JS 动态生成（innerHTML 模板/`el.id=…`）里真实存在。
  - 进了 `self_validate`（自升级验收硬门之一，结果带 `ui_refs` 字段）；
  - 也进了不变量（`invariants.py`，现在 24 条）；
  - 白名单 `ALLOWED_MISSING` 只有 `sid`（旧字段的防御性兜底），不许静默膨胀。
  专门拦"改了 JS 逻辑却忘了加元素"这类假完成（task-mvs4un2x 实测的坑）。

### Added（测试）

- `tests/test_ui_id_refs.py`（7 条）：缺漏检出、动态生成 id 认可、行号回报、
  现网零缺漏、白名单不膨胀、`self_validate` 包含本检查。

## [11.0.0-preview.19] - 2026-10-28

### Added

- **心跳/示波器动画的设置开关**（用户用自升级命令做的功能，收尾补全）：设置 → 外观
  → "任务进行时的心跳动画（卡片上沿的示波器）"，默认开；关掉后 `startScope` 立即返回，
  不再产生动画帧（实测关后 rAF=false、开后 rAF=true）。

### Fixed

- **自升级任务"完成得太早"**：自升级代理（task-mvs4un2x）加了 `startScope` 的开关判断
  和 `saveSettings` 的持久化，却**忘了把复选框元素加进设置页**就宣布完成
  （它的验证只有 py_compile + unittest，覆盖不了"元素存在"）。我补上了外观区的复选框
  与 i18n，现在设置页真能看见了。

## [11.0.0-preview.18] - 2026-10-28

### Fixed

- **自升级回滚列不出快照**（用户：`python scripts/rollback.py --list --scope self`
  只得到"没有可回滚的服务自身代码快照记录"）。两个原因：
  1. **从来没写过**：agent.db 里 `self_replace_text` / `self_write_text_b64` 的历史
     调用是 **0 次**（只有 self_read_text / self_search_text），没有写操作自然没有原像；
  2. **真 bug**：`snapshots._dir()` 为防 `.`/`..` 把"非字母数字开头"的名字加了 `s`
     前缀，连带把自升级作用域 `_self_<sid>` 改成 **`s_self_<sid>`** —— 而
     `rollback.py --scope self` 与 `rollback_self_upgrade(use_last)` 都按 `_self_`
     前缀找 → 永远匹配不到（只有 `--sid` 能恢复，因为它走 `_dir` 同一条路）。
     修：`_dir()` 只拦危险开头（`.`/空），`_` 开头原样保留；前缀匹配处**两套都认**
     （`_self_` 与旧 `s_self_`）；CLI 的空提示补一句"只读排查不会留快照"。

- **单测卡死（真机实测）**：上游"修重启"把 `exec_restart` 从 `os.execv` 改成
  `Popen + os._exit(0)`，但 `tests/test_restart_service.py` 仍按旧契约（patch execv、
  期待 SystemExit）写 → 测试里**真的 spawn 了** `python start.py -s -v`，
  父进程 `os._exit(0)` 立刻退出、spawn 的服务还活着 → 外部看起来"跑不完/卡住"。
  修：测试改成新契约（patch Popen + os._exit）；`_entry_argv()` 不再透传任何参数
  （start.py 不解析 argv，透传只会把 `-s -v`/`discover`/`tests` 带进去）。

### Verified

受控实验（只在 `docs/` 下建临时文件，不碰真实代码）：
`self_replace_text` → 快照目录 `_self_probe-self-snap2` → CLI `--list --scope self`
列出 → CLI `--sid … --yes` 回滚成功（还原 1、失败 0）→ 文件回到原内容。

### Added（测试）

- `tests/test_self_snapshot_scope.py`（5 条）：`_self_` 不加前缀、`scopes("_self_")`
  能列出、`.`/`..` 仍被挡、旧 `s_self_` 仍被认、CLI 两套前缀。

## [11.0.0-preview.17] - 2026-10-27

### Added

- **发送按钮运行中变"强行停止"**（仿 DSH）：任务在跑时发送键变成红色方块，点一下
  走 `actions/cancel` 停掉当前任务；停止态/发送态由 runtime.busy 实时切换。
- **聊天框 `@` 唤起工作区文件选择**（DSH 的 @ 体验）：输入 `@` 弹出文件列表
  （`/v1/agent/workspace/files?q=`，工作区守卫、跳过 .git/__pycache__/data），
  ↑↓/Enter 选择插入 `@路径`；发消息时后端把 `@路径` 展开成文件内容随消息一起发
  （`gantry/atrefs.py`：围栏内不展开、越界不认、单文件 ≤20k、合计 ≤60k、最多 4 个），
  新任务和跟进消息都支持。

## [11.0.0-preview.16] - 2026-10-27

### Fixed

- **长期记忆"消失了"**（用户实测好久没更新）。根因不是坏了，是**模型从不调
  `memory_write`**：全库历史步骤里它只被调用过 1 次（其余记忆是评测基线/手动塞的）。
  提示词那句"任务收尾判断要不要记"是软提示，模型忙起来永远忽略。

### Added

- **记忆提醒（memory_nudge）**：与 noop_guard 同一模式——任务**实质产出 ≥3 个动作**
  且本任务一条 `memory_write` 都没记过时，第一次返回 final 拦一次，提醒
  "若有跨任务价值的事实请记一条；没有就明说没有"；**只拦一次**（fail-open），
  之后放行。写 1~2 个文件的小任务不打扰（阈值 ≥3，实测批量写两文件不被拦）。
- 设置 → 执行 → "收尾提醒写记忆" 开关（默认开，`agent.memory_nudge`）。

### Added（测试）

- `tests/test_memory_nudge.py`（4 条）：拦截块存在、fail-open 标记、设置页开关、
  产出工具清单。

## [11.0.0-preview.15] - 2026-10-27

### Added

- **Telegram 插件**（用户想要"手机端操作 Gantry"，类似 Hermes 的 bot 交互）：
  `plugins/telegram/`（manifest + plugin.py + README + 15 条测试）。
  - 长轮询 `getUpdates`，不需要公网地址（@BotFather 建 bot 拿 token 即可）；
  - **白名单 chat_id 才受理**（bot 是公开的，必须限制）；token/白名单从 `.env`
    （`TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_CHAT_IDS`）或插件配置读；
  - 一个 chat 映射一个会话 sid（`/new` 强制开新），普通文字继续当前会话（agent 跑），
    完成把回答整段发回，进行中按节流发进度；
  - 命令：`/new /status /sessions /todo /cancel /pause /resume /model [id] /answer /help`；
  - `GET /v1/plugins/telegram/api/status` 面板看健康状态；
  - 无 token 时插件待命不报错。

## [11.0.0-preview.14] - 2026-10-27

### Changed

- **代码明文单次写入上限 1500 → 6000**（用户："1500 是不是太少了"）。
  1500 是"防 Copilot 网页输出截断"的保守值；实测它的回答能装下远不止 1500，
  真正踩坑的是 10KB+ 的大块。6000 仍在安全区，小文件不再被逼着走 Base64 分块。
  - 上限仍可在 config.json 里改（`plain_code_limit`），现在也进了设置页
    （设置 → 执行 → 代码明文单次写入上限）；
  - `AGENT_PROTOCOL`（给模型看的规则）里的数字同步；
  - 超限错误信息不变（仍指路到 `write_text_b64`/`append_text_b64` 分块）。
- 测试里的"超限用例"载荷从 2400/3000/2000 字符调到 8400/7000/7000
  （>6000 才真超限），断言数字同步。

## [11.0.0-preview.13] - 2026-10-27

### Added

- **`.env` 密钥文件**（用户提议）：密钥放 `.env`（已入 `.gitignore`），
  `config.json` 里用 `"${VAR}"` 引用。
  - 读取优先级：**系统环境变量 > .env > config.json 字面量**（用户明确要求：
    系统已有的环境变量**不会**被 .env 覆盖——`load_env_file` 只补缺）；
  - 支持 `#` 注释、`export` 前缀、单/双引号、行内注释；
  - 引用了但没设置的 `${VAR}` 保留原样并记 warning（不静默置空，避免把 token 抹成空串）；
  - config.json 里仍有明文敏感字段（key/token/secret/password）时启动日志提醒。
  - 模板：`.env.example`；`config.example.json` 的 api_token 示例改为 `${GANTRY_TOKEN}`。
- **config.json 从 git 脱钩**（`git rm --cached`）：它历史上被跟踪过，这是密钥泄漏的
  唯一通道（`.gitignore` 忽略了它但已在跟踪里照样入库）。文件保留在盘上，只是不再入库。

### Added（测试）

- `tests/test_envfile.py`（6 条）：系统优先、注释/引号/export 解析、占位符解析与
  未设置保留、明文敏感字段点名、加载顺序（.env 先于 config.json）。

## [11.0.0-preview.12] - 2026-10-27

### Fixed

- **危险区开关拨了没用**（全链路实测）：`allow_self_upgrade` 历史位置是 config.json 的
  **agent 段**（`tools.py` 从 `AGENT_CFG` 读、提示文案也写 `agent.allow_self_upgrade`），
  而 schema 键被我误写成顶层 → UI 写顶层、注册表读 agent 段 → 开关永远不生效。
  修法：schema 键改为 `agent.allow_self_upgrade`（danger 组），live-apply 写回 `AGENT_TOOLS`；
  撤掉此前加的"顶层并入 agent 段"（它把用户配置漏进了测试环境，炸了 13 个测试）。
- **`self_find_files`/`self_search_text` 调不动**：只注册了描述，没进分发表
  （`self._tools`）和工具分组（`TOOL_GROUPS["self"]`）→ 模型一调就报错，误判成
  "没提供自升级工具"，退回工作区乱翻。三处（描述/分组/分发）现在由测试钉住一致性。
- **自升级任务跑去工作区**（用户实测"你去到我的 wgime 项目里去了"）：
  协议默认"路径相对于当前工作目录"，模型据此把工作区当目标。现在任务文本含
  `[自升级任务]`/`自升级` 时额外注入一段强提示：目标代码在安装目录、不要碰工作区、
  搜不到就停下来问。英文模式有译本（`prompt_lang.EN.self_upgrade_lead`）。
- **英文模式下自升级段仍是中文**：`_self_upgrade_rules` 没走 `localize`，
  实测把英文模式的中文占比顶到 6.00% 压线。补了 `prompt_lang.EN["self_upgrade"]` 译本。
- **self_* 工具缺英文描述**：开关开着后它们进了工具清单，`tool_i18n` 没条目
  → 英文界面露中文工具说明。补齐 9 条描述 + 16 条参数。

### Verified

- 模糊任务实测（不提文件名）：`self_search_text` 直接命中 `gantry/agent.py`，
  随后 `self_read_text` 读源码，**零工作区工具**、零乱猜路径，13 秒完成。
  回答正确定位到限流处理分支、`retry_resume_at`、suspended 状态。

### Added（测试）

- `tests/test_self_upgrade_wiring.py`（3 条）：键必须在 agent 段、只有一个 agent.* 分支、
  开关开着时 self_* 工具必须可调用。
- `test_self_search.py` 新增注册一致性（描述/分组/分发表三处必须齐全）。

## [11.0.0-preview.11] - 2026-10-27

### Fixed

- **改名后自升级"按文件夹名猜路径"**：模型去找 `gantry-main` 这类不存在的目录
  （用户实测）。`global_root` 本就由 `__file__` 反推、与文件夹名无关，但提示词没告诉
  模型——现在自升级规则里**注入真实安装目录**（"文件夹名可能还是旧的，不要按名字猜路径"），
  `/self-upgrade` 模板加"不记得文件名就先 self_search_text 定位、报给你确认后再改"。

## [11.0.0-preview.10] - 2026-10-27

### Changed

- **四行展开高度调大**（待办/子代理/调用记录/状态）：级联底部一路压小的旧规则
  （最小到 11vh/132px）由末尾的 ROWS-TALLER 覆写块统一抬到 min(38vh, 400px)、
  调用记录 min(38vh, 420px)，卡片总高 min(74vh, 860px)。
  950px 视口实测：三行各 361px、调用记录 361px、卡片 703px；布局审计无溢出。

## [11.0.0-preview.9] - 2026-10-27

### Added
- 

### Changed
- 

### Fixed
-

## [11.0.0-preview.8] - 2026-10-27

用户："我不记得具体的文件和路径，模糊的说法（让 Agent 自升级）不成功吗？"

诚实答案：之前**不行**——自升级工具只有 read/write/replace/validate/unittest/rollback，
**没有搜索/列举**，agent 拿到模糊描述连"代码在哪个文件"都不知道。本版补齐。

### Added

- **`self_find_files(pattern)`**：glob 列举服务目录（如 `gantry/*.py`）——"先定位"第一步；
- **`self_search_text(query, path, max_results)`**：在服务目录里搜文本/正则，返回
  file:line + 内容——"那个按钮/那个报错"先搜出来再改；
- `/self-upgrade` 模板加"**不记得文件名就先调 self_search_text 搜**，把定位报给你确认后再改"。

### Fixed

- **白名单在 `allow_self_upgrade=False` 时构造为空**（`tools.py:265`）：危险区开关即时生效，
  但运行时把开关打开会遇到"允许了却什么文件都不让碰"。现在白名单**始终构造**，
  与开关解耦（`_self_path` 本就先查开关）。

### Added（测试）

- `tests/test_self_search.py`（5 条）：搜索/列举在开关打开时可用、关闭时被拒、
  黑名单（config.json）搜不到、新工具已注册进工具列表。

## [11.0.0-preview.7] - 2026-10-27

### Added

- **设置 → 危险区：「允许 Agent 修改服务自身代码（自升级）」开关**（用户要求）。
  原有 `allow_self_upgrade` 只能手改 config.json；现在进了设置页（默认关），
  且**即时生效**（`/v1/config` 保存时直接写回 `AGENT_TOOLS`，不用重启）。
- **斜杠命令 `/self-upgrade`**（别名：自升级/改服务）：一键把中文自升级任务模板
  填进输入框并切到 Agent 模式（手册里原有的英文模板做成了"方便直接填"的命令）。
- **维护端点 `POST /v1/agent/copilot/cleanup-marked`**：本地记录删了之后，也能按
  会话标记清理远端 Copilot 会话（`scan_only` 先列后删；`delete_matching` 逐行删）。
  侧栏扫描支持**滚动**（虚拟列表只渲染可视行，不滚会漏——实测删一轮还剩残留）。

### Fixed

- **浏览器启动失败（`ERR_ABORTED`，页面停在 about:blank）**：首次启动的
  `page.goto(..., wait_until="domcontentloaded")` 没有容错——企业租户的 SSO 会在
  domcontentloaded 之前就重定向走，Playwright 报 `ERR_ABORTED`。复用窗口/新窗口两处
  goto 都有兜底，唯独首次启动漏了。现在 ERR_ABORTED 视为正常（交给就绪检测接手）。
  `tests/test_browser_boot_aborted.py`（2 条）钉住三处 goto 都必须容错。

## [11.0.0-preview.6] - 2026-10-26

处理 GitHub issue #2 / #3 / #4：

### Added

- **issue #2 `power-automate` 技能**：新目录 `skills/power-automate/`
  （`SKILL.md` + `templates/flow-definition.json` + `templates/connections.json`）。
  教 Agent 生成 Power Automate 工程：同租户 automate 环境（make.powerautomate.com）、
  `definition.json` 结构（triggers/actions/parameters/runAfter 状态机）、
  同租户内置连接器引用（Office 365 Outlook / SharePoint / Teams / Dataverse）、
  交付目录 `flows/<flow-name>/`，并要求生成后跑 `json.load` 校验。
  明确禁止真实 API 调用（没有凭据）——技能只负责"产出正确工程"。
- **issue #3 README 三语版**：默认 `README.md` 改为**英文**；新增 `README.zh-CN.md`
  （简体）与 `README.zh-TW.md`（繁体，台湾惯用字），每份顶部有语言切换行。
  翻译由 4 个子代理分段完成，规则：代码块/命令/API 路由/config 键名/路径/URL/表格
  结构原样保留，只翻译散文与表格单元格。

### Verified

- **issue #4 Copilot 多任务并发检验**：临时把 `agent.copilot_windows` 开到 2，
  并发发两个任务（"只回一个词：ALPHA/BETA"）：两条都成功、耗时几乎相同
  （138.0s vs 138.8s，串行的话后者应 ~2×）、回答与各自提示词一致、`windows=2`，
  收工后把窗口数收回 1 并清理诊断会话。结论：**并发正常**。

### Added（测试）

- `tests/test_readme_langs.py`（5 条）：三份 README 存在、互指、标题统一、围栏成对、
  默认版前 30 行全英文。
- `tests/test_skill_power_automate.py`（4 条）：技能可发现/可读、模板 JSON 可解析、
  SKILL.md 覆盖同租户环境与交付目录。

## [11.0.0-preview.5] - 2026-10-26

### Fixed

- **「重启服务」因路径问题失败**（用户报障）。三个坑：
  1. **入口解析用 `sys.argv[0]`**：用 `python -m gantry.cli serve` 或 `uvicorn app:app`
     启动时，`sys.argv[0]` 是**包内的 `gantry/cli.py`**；`os.execv` 把它当脚本重跑 →
     `from .brand import env` 立刻 `ImportError: attempted relative import with no known
     parent package`，重启后的进程直接死掉，WebUI 等 90 秒报"未恢复"。
     现在入口**固定**为项目根的 `start.py`（由包位置反推绝对路径），只在没有 `start.py`
     （装成纯包）时退回 `-m gantry.cli serve`；`serve` 这类子命令不再被当参数带过去。
  2. **cwd 依赖**：`exec` 不改变工作目录，而 `start.py` 的 `uvicorn.Config("app:app")`、
     `config.json`、`data/` 都相对 cwd。现在 `exec_restart()` 先 `os.chdir(项目根)`，
     派生兜底也用 `cwd=项目根`；`start.py` 还额外把项目根插入 `sys.path`。
  3. **`gantry serve --port N` 静默无效**：只写了环境变量，`start.py` 从不读它。
     现在端口优先级 = `GANTRY_PORT`（兼容 `CBP_PORT`）> `config.json` > 8765。

### Added

- `tests/test_restart_paths.py`（8 条）：入口必须是项目根的绝对 `start.py`、绝不能是包内
  `.py`、模拟 `-m gantry.cli serve` 启动、无 `start.py` 时退回模块启动、
  `exec_restart()` 必须 `chdir`、端口必须优先取环境变量。

实测（临时端口 8799，不动在跑的 8765）：`POST /v1/restart` → 200 → 90 秒内
`/health` 恢复、版本一致；`gantry serve --port 8798` 确认绑定 8798。

## [11.0.0-preview.4] - 2026-10-26

### Fixed

- **issue #1 `Locator.press: Timeout 30000ms exceeded`**（用户中午提的 issue）。
  日志显示 locator 已解析到 Lexical 编辑器
  `<span data-lexical-editor="true" id="m365-chat-editor-target-element">`，
  但 `elementHandle.press("Enter")` 等满 30 秒超时。

  根因：`send()` 在 `fill()` 之后继续用**同一个句柄**按键，而 Copilot 的 Lexical
  编辑器在输入后会重渲染，旧 `<span>` 脱离 DOM —— Playwright 的可操作性检查
  永远等不到元素，30 秒后抛错（表现为任务卡半分钟后失败）。

  修法（三级兜底，且每级都缩短超时，失败要快而不是干等 30s）：
  1. 当前句柄 `press(keys, timeout=6s)`；
  2. 失败 → 重新 `input_box()` 取新句柄再按；
  3. 再失败 → `page.keyboard.press(keys)`（走当前焦点元素，不依赖旧句柄）。

  `Control+A` / `Enter` / `Control+Enter` 全部改走 `_press_box()`；提示词写入改走
  `_type_prompt()`（`fill` → 重新取句柄逐字 → `keyboard.type`）。

### Added

- `tests/test_press_fallback.py`（5 条）：句柄失效时必须在**新句柄**或
  `page.keyboard` 上重试，且 `send()` 不得再用可能失效的句柄直接 `press`。

## [11.0.0-preview.3] - 2026-10-26

审核遗留项收尾（用户确认"都改完"并给 token 加了 `workflow` scope）：

### Changed

- **安装名也改成 Gantry**：`pyproject.toml` 的 `name` 从 `copilotbridge-agent` 改为
  `gantry`（`import gantry` 与 CLI 入口 `gantry` 本来就已经是 gantry）；
  补 `[project.urls]`（Homepage / Repository / Changelog）。
  迁移：`pip uninstall copilotbridge-agent` → `pip install -e .`（或 `pip install gantry`）。
- **CI 发布产物改名**：`.github/workflows/release.yml` 的暂存目录、压缩包、artifact、
  Release 标题全部 `CopilotBridge_*` → `Gantry-*`。
- **发布前加 fail-closed 校验**：`tag == gantry/version.py 的 VERSION`，且
  `CHANGELOG.md` 必须有同名小节（`docs/VERSIONING.md §6` 要求 1:1）。
  位置在"解析版本"之后、构建之前；用 `pwsh` 写（整个 job 是 `windows-latest`，
  最初的 bash 写法在 Windows runner 上会直接失败——这是审核指出的另一处）。

### Fixed

- `release.yml` 的校验 step 最初被追加到文件末尾（构建之后）且语法是 bash，已改正。

## [11.0.0-preview.2] - 2026-10-26

改名收尾（多 agent 交叉审核发现的问题，逐条修）：

### Fixed

- **搜索高亮丢样式**（真实回归）：CSS 只定义 `mark.cbp-hit`，而 app.js 生成
  `class="gantry-hit"` —— 改名时只扫了 `.py/.js/.html/.md`，漏了 `.css`。
  现在类名/动画名统一为 `gantry-*`。
- **旧 Copilot 会话删不掉**：删除时用新标记 `[GANTRY-AGENT][sid]` 做归属校验，
  改名前的旧会话只有 `[CBP-AGENT][sid]` → 被判"不属于本会话"，必现误拒。
  现在 `brand.marker_prefixes()` 同时给出两套前缀，归属校验与对账终验都认两套。
- **旧请求头被覆盖**：`routes_sessions` / `routes_core` 只读新头 `X-Gantry-Lang`，
  旧客户端带 `X-CBP-Lang` 时会被重新设成 zh；改用 `a._lang_of()`（两套都认）。
- **`GANTRY_*` 环境变量此前是死代码**：`brand.env()` 无人调用。现已接入
  `cli.py`（URL/TOKEN/PORT）、`agentrun.py`（EVENT_SOURCE）、插件与脚本；
  `GANTRY_*` 优先、`CBP_*` 回落。`gantry serve --port` 现在同时写两个变量名。
- **升级后丢 token / 语言 / 主题**：前端只读 `gantry-*`，没有旧键回落。
  `boot.js` 增加一次性迁移（`cbp-*` → `gantry-*`，带 `gantry-migrated` 标记），
  `apiToken()` 也回落 `cbp-token`。
- **英文界面仍有中文**：19 个 `tr()` key 没有英文条目（`版本`/`调用`/`回答`/`已复制`…），
  `等待模型回答…` 完全没走 `tr()`；已补齐。
- **繁体漏字**：S2T 表缺 `实→實`、`东→東`（"实時流"、"不能改任何东西"），已补。
- 界面残留旧名：页签标题兜底、导出 JSON 的 `app` 字段、`--help` 描述、包 docstring、
  自升级提示词、出站 `User-Agent`、首页徽标（`CB` → `G`）。

### Changed

- 启动日志现在带品牌与版本：`Gantry v11.0.0-preview.2 已启动（…）`。
- 文档/CI 元数据：README 版本行与克隆地址、`docs/TECHNICAL.md` 标题与标记名、
  `PROVIDER-deepseek-web.md` 项目名、`MULTI-WINDOW.md` 过期版本头、
  `release.yml` 产物/artifact/Release 名称；`requires-python` 与 CI 统一到 3.11。
- `bump_version.py` 现在也能同步 README 里 `**x.y.z**` 形态的版本行
  （此前只匹配全角括号，README 从没被同步过）。

### Added

- `tests/test_rename.py`（14 条）钉住上述回归：类名一致性、旧标记双认、
  `brand.env` 优先/回落、localStorage 迁移、首页徽标、英文覆盖率、启动横幅。

## [11.0.0-preview.1] - 2026-10-26

### Added

- **项目更名为 Gantry**（原 CopilotBridge Agent）：包名 `cbp/` → `gantry/`，
  界面/文档/启动日志统一品牌；`GET /` 返回 `name=Gantry`。
- 兼容层 `gantry/brand.py`：新名字优先、旧名字仍可用——
  - Copilot 会话标记新写 `[GANTRY-AGENT][sid][Cnnn]` / `[GANTRY-TURN:…]`，
    **读**时同时认旧的 `[CBP-AGENT]` / `[CBP-TURN]`（否则旧会话删不掉、对不上账）；
  - 环境变量 `GANTRY_*` 优先，回落 `CBP_*`；
  - 请求头接受 `X-Gantry-Lang/Token`，同时兼容 `X-CBP-Lang/Token`；
  - 前端 localStorage 用 `gantry-*`，读不到时回落旧键 `cbp-*`。

### Changed

- **BREAKING**：Python 包名从 `cbp` 改为 `gantry`。外部脚本若 `import cbp`、
  或写死了 `cbp/api.py` 之类的路径，需要改成 `gantry`。
  由环境变量/请求头/标记驱动的调用不受影响（上面的兼容层）。
- `pyproject.toml` 的包名仍为 `copilotbridge-agent`（安装名），CLI 入口 `gantry`。

## [10.3.0-preview.3] - 2026-10-26

### Added

- **文件直读 + 文件卡片**：说「给我看下 docs/x.md」时由本地读出并渲染成卡片
  （文本带行号可折叠、图片直显、二进制给下载），支持简中/繁中/英文动词，
  也支持「原文 / 这个文件 / 刚才那个」指代上次展示过的文件。
- **回合流**：调用记录按回合展示「发送 → 回答 → 工具调用」，提示词正文落库
  （`turn_results.prompt`），新增 `GET /v1/agent/sessions/{sid}/turns`。
- **版本管理**：版本号单一来源 `gantry/version.py`、`scripts/bump_version.py`、
  本 CHANGELOG、`docs/VERSIONING.md`、`.gitattributes`（统一 LF）。

### Changed

- 待办 / 子代理 / 调用记录 / 状态四行合并进同一个 `#teamCard`；去掉分隔线，
  任务进行中改由**示波器浮层**（canvas、鼠标穿透、不占布局、跟随主题色）呈现。
- 调用记录改为行内展开（原浮层弹窗），默认只画最近 20 回合，可「显示更早的」。
- 会话记忆：Agent 提示词附带本会话最近 6 条对话。
- 对话区保底高度；`#teamCard` 不再强制最小高度（消除收起时的空白）。
- 模型下拉可用性改为**并集**：菜单扫描只做"新增可用"，不再把没扫到的模型判成不存在。

### Fixed

- **跟进消息从未进入提示词**：`pending.append(task)` 曾被误缩进到 `except` 里，
  导致追加指令不生效、模型一直在旧任务上打转（会话 task-mvn6jjrt 实录）。
- **把上一次的最终答复当成新回答**：去掉 `generation_seen` 后门，并用
  `[CBP-TURN:…]` 标记切分，只取本回合文本。
- **`gantry.bridge` 缺 `_focus_box`** 导致任务起不来；输入框聚焦加三级兜底
  （短超时点击 → 关浮层再点 → `el.focus()`）。
- **「重试远程并强制清理本地记录」按钮无反应**：本地已删后 `current` 为空，
  按钮静默返回；现在记住 `_sid/_urls`，并支持本地不存在时仅重试远端删除。
- `noop_guard` 改为按**最新一条指令**判断，纯分析请求不再被逼着产出文件。
- 状态条 `failed` 时不再显示上一句"已完成"；空回答不再渲染「回答（空）」。
- 英文模式下的状态详情不再露中文（前端 `DETAIL_EN` 覆盖 28 条后端模板）。
- 清理诊断遗留消息、修正会话 `last_view_file` 字段的存储白名单。

## [10.3.0-preview.2] - 2025-10-24

### Added

- 多窗口并行：`agent.copilot_windows` 1~4，`WindowPool` / `_PageProxy` / 窗口池复用。
- 提供商模型接入（DeepSeek / Kimi / Grok / OpenAI 兼容），`plugins/deepseek-web`。
- 托盘/自动化浏览器稳定性改进（新窗口就绪等待、SSO 未完成不误判）。

### Changed

- 底部四条横条统一为 `.urow` 行样式（36px 等高、图标 + 标签 + 计数 + 折叠箭头）。
- 设置面板按执行/模型/界面重排；i18n 覆盖扩展到所有设置页。

## [10.2.1] - 2025-10-20

- 归档版本：tag `v10.2.1-archive`、分支 `archive/10.2.1-stable`。
  多窗口合并前的最后一条稳定线。
