# visual-design 插件技术设计方案

日期：2026-11-19  
状态：**已确认范围，按本方案实施**  
范围：只分析/规划，不修改 Gantry 核心代码。

## 1. 源码分析结论

### 1.1 插件系统

读取了：

- `AGENT.md`
- `gantry/plugins.py`
- `plugins/image-gen/plugin.py`
- `plugins/mcp/plugin.py`
- `gantry/tools.py`
- `gantry/agentrun.py`

关键事实：

| 事实 | 影响 |
|---|---|
| `PluginManifest` 支持 `plugin.json` 平铺或 `package.json` 的 `gantry` 字段 | 插件用 `plugin.json` 即可 |
| `PluginContext.register_tool(name, fn, description, arguments, approval=False)` | 六个工具在这里注册 |
| `setup(ctx)` 必须**同步**；async 返回值当前不会 await | 不能在 setup 里 await Playwright 启动 |
| `teardown(ctx)` 同步执行 | 不能依赖异步清理；渲染必须每次自清理 |
| `ToolRegistry.execute()` 对插件工具不做参数类型强校验 | 插件内部必须做完整验证 |
| `MUTATING_TOOLS` 是核心静态集合，插件工具不在里面 | 插件必须自己检查 `plan_mode` / `sandbox_mode` |
| `ctx.save_file()` 最终走全局 `AGENT_TOOLS.root` | **不能直接用它保证当前会话工作区** |
| `bridge.task_tools()` 是 contextvar 的当前任务注册表 | 插件应通过它拿当前会话的 `root` |
| 插件工具是普通 callable，不随 AgentRunner clone 重绑定 | 不能依赖 `ctx.host.registry.root` 作为当前工作区 |
| 插件默认可以在工具清单中增加 6 个工具 | 需要控制描述长度，避免提示词膨胀 |

### 1.2 工作区机制

`ToolRegistry._path()`：

- 解析后必须位于 `self.root` 内；
- 有 `workspace_roots` 白名单；
- 有服务安装目录 denylist；
- 支持 `read-only` / `workspace-write` / `danger-full-access`。

**设计决定：visual-design 不复用 `_path()` 的“danger-full-access 可越界”行为。**
插件独立做严格 workspace 校验，所有路径必须位于：

```text
<current workspace>/visual-projects/<project_id>/
```

即使 Gantry 当前是 `danger-full-access`，插件也不允许写到项目目录之外。

### 1.3 当前会话根目录

`agentrun.py` 为每个任务 clone 工具注册表并 `set_root(workspace)`。  
插件工具是注册在全局 `AGENT_TOOLS` 上的普通函数，不会被 clone 重绑定。

因此插件内部统一用：

```python
from gantry import bridge as b
registry = b.task_tools()          # 当前任务的 registry（contextvar）
workspace = Path(registry.root).resolve()
```

没有任务上下文时（例如直接 HTTP API）回退到 `ctx.host.registry.root`。  
**不把 `ctx.save_file()` 作为项目文件写入通道**，避免写到错误工作区。

### 1.4 现有 image-gen / mcp 的参考

- `image-gen`：异步工具 + `ctx.save_file()` 模式；它的文件落盘受全局工作区限制，不适合本项目。
- `mcp`：stdio 子进程需要处理 Windows 管道/超时；visual-design **不使用 MCP、不启动 shell**。

### 1.5 依赖

- `playwright` 已在 `requirements.txt`，`install.py` 安装 Chromium。
- Pillow 当前环境有，但**不在 `requirements.txt`**。
- 插件不能假设 Pillow 一定存在，必须有明确错误提示；建议新增插件级 `requirements.txt`，或在确认后把 Pillow 加入核心依赖（本阶段不改核心）。

## 2. 总体架构

```text
Agent 工具调用
  ↓
plugins/visual-design/plugin.py
  ├─ 参数验证 / 路径沙箱 / 项目状态
  ├─ design-spec.json（尺寸、标题、渲染设置）
  ├─ source/index.html 或 source/index.svg
  └─ renderer.py（固定渲染器）
       ├─ Playwright + 独立 Chromium（无头）
       ├─ 网络阻断 / file 资源白名单
       ├─ 固定尺寸截图
       └─ Pillow 检查 / JPG
```

原则：

1. 不修改 Agent 循环。
2. 不使用 Copilot Edge 的持久化上下文。
3. 不启动任意 shell；渲染只调用固定 Python 代码。
4. 设计规格与绘图源文件分离。
5. 每次渲染使用独立的浏览器上下文，结束后关闭。
6. 先写临时文件，成功后再原子替换，避免半成品覆盖旧输出。

## 3. 插件文件结构

```text
plugins/visual-design/
├── plugin.json          # 清单：tools/files 权限、六工具、default_enabled=false
├── plugin.py            # setup/teardown + 六个工具 + 项目/路径/校验
├── renderer.py          # Playwright/Pillow 固定渲染器
├── README.md            # 安装、启用、限制、示例
└── requirements.txt     # Pillow>=10,<14（Playwright 由核心提供）
```

开发期插件放在 `plugins/visual-design/`；发布验收另打：

```text
dist/visual-design-0.1.0.zip
```

原因见“安装/卸载冲突”。

## 4. 设计项目格式

每个项目独立目录：

```text
workspace/visual-projects/{project_id}/
├── brief.md
├── design-spec.json
├── source/
│   ├── index.html
│   └── index.svg
├── assets/
├── renders/
├── output/
└── history/
```

`project_id`：

```text
^[a-z0-9][a-z0-9_-]{0,63}$
```

不允许自动生成项目 ID 以外的路径。未传 `project_id` 时生成：

```text
vd-YYYYMMDD-HHMMSS-<4 hex>
```

`design-spec.json` 示例：

```json
{
  "version": 1,
  "project_id": "poster-2026",
  "title": "六一儿童节",
  "canvas": {"width": 1920, "height": 1080, "background": "#ffffff"},
  "render": {"engine": "chromium", "device_scale_factor": 1,
             "timeout_seconds": 30, "block_network": true},
  "sources": {"html": "source/index.html", "svg": "source/index.svg"},
  "created_at": "2026-11-19T23:00:00+08:00",
  "updated_at": "2026-11-19T23:00:00+08:00"
}
```

约束：

- `design-spec.json` 只存元数据，不嵌入 HTML/SVG；
- 源文件是唯一绘图来源；
- `history/` 保存 modify 前的源文件快照，最多保留 20 份。

## 5. 六个工具

所有工具参数由插件内部验证；错误抛 `DesignError(code, message, hint)`，Agent 能看到结构化错误。

### 5.1 design_create

```json
{
  "project_id": "optional-slug",
  "title": "六一儿童节",
  "width": 1920,
  "height": 1080,
  "brief": "中文宣传海报...",
  "style": "editorial|playful|minimal"
}
```

验证：

| 字段 | 规则 |
|---|---|
| `project_id` | 可空；匹配 slug；不得已存在 |
| `title` | 1–200 字符 |
| `width/height` | 整数，64–4096，且 `width*height <= 16_777_216` |
| `brief` | ≤ 20,000 字符 |
| `style` | 枚举，默认 `editorial` |

行为：

- 创建目录树；
- 写 `brief.md`、`design-spec.json`；
- 生成基础 `source/index.html` 和 `source/index.svg`（可渲染的占位页）；
- 不启动浏览器；
- 返回项目相对路径、spec、源文件。

### 5.2 design_modify

```json
{
  "project_id": "poster-2026",
  "target": "html|svg",
  "mode": "replace|replace_text|append",
  "content": "...",
  "search": "仅 replace_text 使用"
}
```

验证：

| 字段 | 规则 |
|---|---|
| target | `html` / `svg` |
| mode | `replace` / `replace_text` / `append` |
| content | 非空；≤ 2,000,000 字符 |
| search | `replace_text` 时必填，≤ 2,000 字符，必须在源文件中恰好出现一次 |
| 源文件 | 必须存在 |

行为：

- 修改前把原文件复制到 `history/<timestamp>-index.<ext>`；
- 写临时文件，`os.replace` 原子替换；
- 更新 spec `updated_at`；
- `append` 只用于追加，不能绕过大小限制。

### 5.3 design_render

```json
{
  "project_id": "poster-2026",
  "target": "html|svg",
  "output": "poster.png"
}
```

验证：

| 字段 | 规则 |
|---|---|
| target | `html` / `svg` |
| output | 可空；默认 `render-<target>.png`；匹配安全文件名；只能落在 `renders/` |
| 源文件 | 存在且 ≤ 2 MB |
| spec | 存在且尺寸合法 |
| 写权限 | `plan_mode` / `read-only` 时拒绝 |

行为：

- 使用 spec 的 width/height；
- 渲染到 `renders/` 临时文件；
- Pillow 检查实际尺寸/格式；
- 成功后再替换目标；
- 返回 `png`、`width`、`height`、`bytes`、`elapsed_ms`。

### 5.4 design_preview

```json
{
  "project_id": "poster-2026",
  "target": "html|svg",
  "max_side": 1024,
  "output": "preview.png"
}
```

- `max_side`：64–2048；
- 先按 spec 渲染，再用 Pillow 等比缩到最长边 ≤ max_side；
- 输出固定到 `renders/`；
- 不覆盖最终 `output/`。

### 5.5 design_inspect

```json
{"project_id": "poster-2026"}
```

只读：

- 检查 spec、source、renders、output；
- 用 Pillow 返回每个图片的 `format/size/mode/bytes`；
- 检查 spec 尺寸与实际渲染是否一致；
- 返回 `ok` / `issues[]`，不修改任何文件。

### 5.6 design_export

```json
{
  "project_id": "poster-2026",
  "format": "png|jpg|svg",
  "filename": "poster.png",
  "source": "render|source"
}
```

验证：

- format 枚举；
- filename 安全、扩展名匹配；
- 输出只能落在 `output/`；
- 已存在默认拒绝覆盖；需 `overwrite=true` 才替换；
- PNG/JPG 依赖最新 render；SVG 复制 source。PDF 不在本阶段范围。

## 6. 渲染器设计

### 6.1 浏览器

```python
p.chromium.launch(headless=True)
```

- 使用 Playwright bundled Chromium，**不指定 `channel="msedge"`**；
- 不连接 Copilot 的持久化上下文；
- 每次 render 新建 context/page，`finally` 关闭；
- 插件级 `asyncio.Semaphore(1)` 限制并发；
- 捕获启动失败并给出明确错误（未安装浏览器）。

### 6.2 页面安全

- `java_script_enabled=False`；
- `page.route("**/*", handler)`：
  - 允许 `data:`、`blob:`;
  - 允许项目内 `file://` 的 `source/`、`assets/`；
  - **阻断 http/https/ws/wss/ftp 及项目外 file://**；
- 禁止 `iframe`/`object` 访问任意网络资源（路由层阻断）；
- HTML/SVG 不允许 `<script>` 执行。

### 6.3 尺寸

```python
context = await browser.new_context(
  viewport={"width": w, "height": h},
  device_scale_factor=1,
  java_script_enabled=False,
)
page = await context.new_page()
await page.goto(file_uri, wait_until="load", timeout=nav_timeout)
await page.evaluate("document.fonts.ready")
await page.screenshot(path=tmp_png, full_page=False)
```

- `device_scale_factor=1` 保证 PNG 像素尺寸 = 画布尺寸；
- 1920×1080 → PNG 1920×1080；
- 失败只写临时文件，不覆盖旧输出。

### 6.4 SVG

- 读取 `source/index.svg`；
- 校验 XML 根元素和大小；
- 用 `page.goto(file://...svg)` 或嵌入 data URI 的 `<img>`；
- 禁止 SVG 内脚本/外部图片；
- 按 spec 尺寸截图。

### 6.5 JPG

- JPG：Pillow 从 PNG 转 RGB，质量 90；
- 先写临时文件，成功后 `os.replace`；
- 输出上限 25 MB。

### 6.6 超时与资源

| 项 | 默认 | 限制 |
|---|---|---|
| 单次渲染总超时 | 30s | 1–120s，插件 config |
| 页面导航 | 10s | 固定上限 |
| 截图 | 10s | 固定上限 |
| 并发渲染 | 1 | semaphore |
| 画布 | — | ≤ 4096×4096 且 ≤ 16M 像素 |
| HTML/SVG 源 | — | ≤ 2 MB |
| 输出 | — | ≤ 25 MB |
| 预览最长边 | 1024 | 64–2048 |

超时/异常时：

```text
关闭 page/context/browser
删除临时文件
抛 DesignError("RENDER_TIMEOUT"/"RENDER_FAILED")
保留上一次成功输出
```

## 7. 安全边界

### 7.1 路径

插件自有 `project_path()`：

```text
workspace_root = b.task_tools().root
projects_root = workspace_root / "visual-projects"
project_dir = projects_root / project_id
```

验证：

1. `project_id` / 文件名正则；
2. `resolve()` 后必须 `relative_to(projects_root)`；
3. 已存在的路径检查 `resolve()`，防止 symlink 逃逸；
4. 新建文件的父目录必须已在项目内；
5. 拒绝绝对路径、盘符、UNC、`..`、空文件名、控制字符。

### 7.2 不执行 shell

- 不使用 `subprocess`/`pwsh`/`run_python`；
- 不通过 Agent 生成命令；
- Playwright 启动 Chromium 是固定库调用；
- Pillow 只在进程内处理图像。

### 7.3 读/写权限

- `design_create/render/modify/export` 是 mutating：
  - `plan_mode` 或 `sandbox_mode=read-only` → 拒绝；
- `design_preview` 写 renders，也受同样限制；
- `design_inspect` 只读；
- 不修改 Gantry 的 `approval_policy` 或安全默认值；
- 插件工具注册 `approval=False`，但通过 workspace-only + 固定渲染器保证边界；若用户要求，可后续把 export 标记为 approval。

### 7.4 HTML/SVG

- JS 禁用；
- 网络请求阻断；
- 只允许项目内资源；
- 禁止外部字体/CDN；
- 文件大小限制；
- 渲染进程独立，不接触 Copilot 页面。

## 8. 安装 / 启用 / 卸载

### 8.1 开发期

```text
plugins/visual-design/
```

通过插件管理器：

- 启用；
- 禁用；
- 重载。

内置插件默认**不能卸载**，这是现有 `PluginHost.uninstall()` 的明确行为。

### 8.2 验收安装/卸载

为满足“可安装、可卸载”，同时不改核心：

1. 开发源码保留在 `plugins/visual-design/`；
2. 构建发布包 `dist/visual-design-0.1.0.zip`；
3. 验收时通过插件管理器从 zip 安装到 `plugins/visual-design`；
4. `PluginHost.install()` 会写入 `installed` 元数据，之后 `uninstall()` 可删除；
5. 卸载后需要恢复开发源码时，从 git 恢复。

如果要求“内置目录本身也能直接卸载”，必须修改 `PluginHost` 或允许 `allow_builtin` 暴露到 UI——这属于核心改动，本阶段不做。

## 9. 测试方案

### 9.1 单元/集成测试

目标文件：

```text
tests/test_visual_design_plugin.py
tests/fixtures/visual-design/...
```

覆盖：

- 正常 HTML 渲染（1920×1080，PNG）
- 正常 SVG 渲染
- 中文字体（HTML 含“六一儿童节”，非空像素检查）
- 指定画布尺寸（Pillow 检查尺寸）
- 文件不存在
- 非法参数（project_id/宽高/format/mode）
- 路径穿越（`../`、绝对路径、symlink 逃逸）
- 渲染超时（低 timeout 注入）
- 渲染失败恢复（已有成功输出，后续失败不覆盖）
- `design_inspect` 尺寸不一致报警
- `design_export` PNG/JPG/SVG/PDF
- 工具注册/卸载后工具消失
- 插件 zip 安装、启用、卸载（临时 `PluginHost`）

### 9.2 端到端

用真实 Agent 任务（WebUI 新任务）：

```text
制作一张 1920×1080 中文宣传海报。
先 design_create，再 design_modify 写 HTML/CSS，
design_render 渲染，design_inspect 检查尺寸，
最后 design_export PNG + 保留 source/index.html。
```

验收：

- Agent 自动发现并调用六个工具；
- PNG 实际 1920×1080；
- 中文正常；
- `source/index.html` 可编辑；
- 失败时 Agent 收到结构化错误并修正。

### 9.3 回归

```text
python -m unittest discover -s tests
python scripts/invariants.py
```

不修改核心，现有测试应全绿。

## 10. 文件改动清单（待确认）

新增：

```text
plugins/visual-design/plugin.json
plugins/visual-design/plugin.py
plugins/visual-design/renderer.py
plugins/visual-design/README.md
plugins/visual-design/requirements.txt
tests/test_visual_design_plugin.py
tests/fixtures/visual-design/...
docs/VISUAL_DESIGN.md
```

构建/分发（验收阶段）：

```text
dist/visual-design-0.1.0.zip
```

可能新增（需确认是否允许，不算核心代码）：

```text
requirements-visual.txt       # 如果不想改核心 requirements.txt
scripts/build_visual_design_plugin.py
```

**不修改：**

```text
gantry/agent.py
gantry/agent_prompt.py
gantry/tools.py
gantry/bridge.py
config.json
现有权限默认值
```

## 11. 实施阶段

1. **方案确认**（当前）
2. 插件骨架 + 路径/项目/六个工具
3. HTML/SVG 渲染器 + Pillow 检查
4. 单元测试
5. 打包 zip + 安装/卸载测试
6. 真实 Agent 端到端验收
7. 全量回归

## 12. 需要确认的问题

1. **安装/卸载验收**：已确认内置目录本身可卸载（通过 allow_uninstall 标记）。
2. **Pillow 依赖**：已确认加入核心 `requirements.txt`。
3. **approval**：已确认不做逐次审批，依赖 workspace/sandbox 限制。
4. **PDF**：已确认不需要。
5. **中文海报 E2E**：已确认纯 HTML/CSS，不需要 AI 插画。
