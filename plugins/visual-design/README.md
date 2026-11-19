# visual-design

Gantry 的 2D 平面设计插件：HTML/CSS/SVG 项目，独立 Chromium 渲染，Pillow 检查，PNG/JPG/SVG 导出。

## 安装与启用

插件源码在 `plugins/visual-design/`，默认 **disabled**。在设置 → 插件管理里启用。

渲染依赖：

- Playwright（核心 `requirements.txt` 已包含，`install.py` 安装 Chromium）
- Pillow（本插件 `requirements.txt`；Windows 环境通常已有）

```powershell
.\.venv\Scripts\python.exe -m pip install -r plugins\visual-design\requirements.txt
```

## 六个工具

```text
design_create  创建 visual-projects/<id>/ 项目
design_modify  修改 source/index.html 或 index.svg
design_render  渲染 PNG（独立 Chromium）
design_preview 生成预览 PNG
design_inspect 检查尺寸/源文件/输出
design_export  导出 PNG/JPG/SVG（本阶段不含 PDF）
```

## 项目结构

```text
<workspace>/visual-projects/<project_id>/
├── brief.md
├── design-spec.json
├── source/index.html
├── source/index.svg
├── assets/
├── renders/
├── output/
└── history/
```

## 限制与安全

- 不启动 shell，不使用 GIMP/Photoshop，不使用 Copilot Edge。
- HTML/SVG 禁用 JavaScript，阻断网络；只允许项目内 `source/`、`assets/` 的本地资源。
- 路径必须在 `visual-projects/<project_id>/` 内，拒绝绝对路径、`..`、symlink 逃逸。
- 画布 ≤4096×4096 且 ≤16M 像素；源文件 ≤2MB；输出 ≤25MB；渲染并发 1。
- 渲染失败不会覆盖上一次成功的输出。

## 快速验证

```text
design_create(title="测试海报", width=1920, height=1080)
design_modify(project_id="...", target="html", mode="replace", content="...")
design_render(project_id="...", target="html")
design_inspect(project_id="...")
design_export(project_id="...", format="png")
```

PDF 不在第一阶段范围内。
