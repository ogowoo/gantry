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

## 设计质量门（必须遵守）

- **不要用 GIMP 的椭圆/折线/贝塞尔基础形状拼人物或复杂插画**。需要插画先获取素材：
  - `image_generate`（可用时）
  - 用户提供的图片
  - SVG/HTML 渲染好的图
  GIMP 负责合成、修图、排版，不负责从零“手绘”人物。
- 文字每个角色只建 **一个** 图层：主标题 / 副标题 / 正文各一层。
  反复重试前先 `gimp_inspect_image` 看已有图层，禁止叠加重复文字。
- 画布固定安全边距（1080×1440 建议 ≥72px）；不要把内容挤在顶部、下面留半屏空白。
- 导出前：
  - `gimp_get_selection` 确认没有残留选区
  - 隐藏参考线/网格，确认没有文本框边线
  - 先 `gimp_preview` 检查重叠、错位、重复文字
- 如果 `image_generate` 返回 401/未配置：**停下来告诉用户**，
  不要改用粗糙几何图形假装完成插画。

## 海报不要这样画

```text
椭圆头 + 线条四肢 + 纯色方块背景 + 重复文字
```

这不是“手绘风”，是几何拼贴。  
没有插画素材时，优先做**排版型海报**（大标题、色块、真实字体），
或先向用户确认是否接受极简几何风格。


## 交付

- 预览图：`gimp_preview`
- 最终文件：`gimp_export_image` 到用户指定路径
- 如果只是测试，创建小图后可以关闭，不要留下无用文档
