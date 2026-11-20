---
name: visual-design
description: 用 visual-design 插件完成 2D 设计：创建项目、编辑 HTML/SVG 源文件、渲染、预览、检查、导出
---

# visual-design 插件

Gantry 的 `visual-design` 插件提供七个工具。它使用独立 Chromium 渲染，不依赖 GIMP/Photoshop。

## 工作流

```text
design_create
  → asset_import（可选：导入 image_generate 的生成图）
  → design_modify（改 source/index.html 或 index.svg）
  → design_render
  → design_inspect
  → design_export（PNG/JPG/SVG）
```

需要缩略图时用 `design_preview`。

## 项目结构

```text
visual-projects/<project_id>/
├── brief.md
├── design-spec.json
├── canvas.json              # canvas/layers/assets 基础结构
├── source/index.html
├── source/index.svg
├── assets/index.json
├── renders/
├── output/
└── history/
```

## 与 image-gen 配合

```text
image_generate(prompt="...")
  → generated/xxx.png
asset_import(project_id="...", source="generated/xxx.png", role="background")
  → assets/asset_xxx.png + assets/index.json
```

`canvas.json` 目前只建立画布、空 layers 和资产 ID 列表；图层级编辑是后续能力，不要把它当作已完成的可视化编辑器。

## 规则

- 先 `design_create`，不要手写目录。
- HTML 和 SVG 都是可编辑源文件；`design-spec.json` 只放尺寸/标题等元数据。
- 1920×1080 等尺寸在 `design_create` 时指定；后续不要用截图工具改尺寸。
- 改源文件用 `design_modify`，优先 `replace_text` 精确替换，避免整篇重写。
- 渲染失败不要反复重试同一份源文件；先 `design_inspect` 或读输出错误。
- 导出 PNG/JPG 前必须先 `design_render`。
- 本阶段不支持 PDF，也不生成 AI 插画；插画由用户提供或先用 image-gen（如配置）。

## 错误代码

常见：

```text
INVALID_PROJECT_ID
PROJECT_NOT_FOUND
SOURCE_MISSING
PATH_ESCAPE
RENDER_TIMEOUT
RENDER_FAILED
SIZE_MISMATCH
OUTPUT_EXISTS
```

按错误代码修正参数或源文件，不要静默忽略。
