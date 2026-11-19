---
name: gimp
description: 通过 GIMP MCP 处理图片：打开/检查/预览、图层、选区、滤镜、导出；先确认 GIMP 3.2 与 bridge 已连接
---

# GIMP MCP 图片处理

## 前提

Gantry 的 `mcp` 插件已连接一个名为 `gimp` 的 MCP server，GIMP 3.2 正在运行，
并且 `gimp-mcp doctor` 显示：

```text
A running GIMP answers on the bridge
```

工具名前缀是 `mcp__gimp__`。

## 第一步

永远先调用：

```text
mcp__gimp__gimp_status
```

确认：

- `connected=true`
- GIMP 版本与 plug-in 版本
- 当前 `images` 列表和 image id

没有图片时，先让用户打开图片，或用 `gimp_open_image` / `gimp_create_image`；
不要凭空猜 image id。

## 常用流程

```text
gimp_status
  → gimp_inspect_image(image)
  → gimp_preview(image)          # 先看当前效果
  → 修改（图层/选区/滤镜/文字）
  → gimp_preview(image)          # 再看结果
  → gimp_export_image / gimp_save_image
```

更多能力：

```text
gimp_list_toolsets
gimp_enable_toolsets(toolsets="...")
```

## 安全约束

- 修改是**真实 GIMP 文档**，不是沙箱副本。
- 覆盖保存/导出前明确确认目标路径；不要默认覆盖原图。
- 大改前用 `gimp_create_checkpoint`，需要时 `gimp_restore_checkpoint`。
- 不要删除图层/关闭图片而不先说明。
- 处理完给用户：image id、预览、导出路径、做了哪些操作。

## 交付

- 预览图：`gimp_preview`
- 最终文件：`gimp_export_image` 到用户指定路径
- 如果只是测试，创建小图后可以关闭，不要留下无用文档
