---
name: poster-design
description: 海报/封面/宣传图的排版规范：先定网格与信息层级，再选素材和字体；禁止用 GIMP 基础形状拼人物，导出前做质量检查
---

# 海报设计

## 什么时候用

用户要求做海报、封面、宣传图、活动图、节日图、社交媒体图时。

## 先定结构，再画

```text
画布：1080×1440（竖版）或 1080×1080
安全边距：≥72px
网格：12 栏或 6 栏；标题、副标题、信息区、落款各占明确区域
```

信息层级最多 3 级：

1. **主标题**：1 个，最大
2. **副标题/主题**：1 个
3. **正文/时间地点**：1 个块

不要出现：

- 两个重叠的主标题/副标题
- 重复创建的文字图层
- 半屏空白、所有元素挤在顶部
- 没有信息层级的纯装饰堆叠

## 素材优先

- 有 `image_generate` 且可用：先出无字主视觉，再在 GIMP/排版层加字。
- 没有生成服务：用 SVG/HTML/CSS 做排版型海报，或使用用户提供的图片。
- **不要**用 GIMP 的椭圆、折线、贝塞尔路径从零拼人物/动物/复杂场景。
  几何风可以，但必须在开始前明确告诉用户“这是极简几何风格”。

## 字体与颜色

- 最多 2 个字体族；中文优先系统可用字体（先 `gimp_list_fonts` 查）。
- 1 个主色 + 1 个辅色 + 中性色；不要超过 4 个高饱和色。
- 主标题字号约为画布宽度的 8%–12%；正文不小于 24px。
- 文字不贴边，不与装饰元素重叠。

## 导出前检查

- 主标题/副标题/正文各只有一层
- 没有残留选区、参考线、文本框边线
- 预览图中没有重叠文字、截断、错位
- 画布尺寸和目标平台一致
- 同时交付 PNG 和可编辑源文件（XCF/SVG/HTML）

## GIMP 里怎么落地

1. `gimp_status` 看会话
2. `gimp_create_image` 建画布
3. 背景层 → 主视觉素材层 → 文字层（各一层）→ 装饰层
4. 每加完一层 `gimp_preview`
5. 有问题先改图层，不要重复建文字层
6. 导出前 `gimp_get_selection` 确认无选区
7. 导出 PNG + 保存 XCF

## 在 Gantry 里生成排版海报（推荐）

不要用 GIMP 基础形状画人物。用 HTML/CSS 排版，再用本仓库的渲染脚本：

```powershell
# 1. Agent 用 write_text 创建 HTML
#    workspace/my-poster.html

# 2. 渲染成 PNG（1080×1440）
.\.venv\Scripts\python.exe scripts\render_html.py `
  workspace\my-poster.html `
  workspace\my-poster.png `
  1080 1440

# 3. present 两个文件
```

在 Gantry 的新任务里可以这样写：

```text
使用 poster-design 技能做一张 1080×1440 的六一儿童节排版海报。
不要用 GIMP 基础形状画人物；先用 write_text 写 HTML/CSS，
再运行 .venv\Scripts\python.exe scripts\render_html.py 生成 PNG，
最后 present PNG 和 HTML。
```

`pwsh` 需要一次授权，允许后脚本输出会返回 PNG 路径和字节数。

## 失败处理

如果 `image_generate` 401/未配置：

- 不要用粗糙几何图假装插画
- 改做排版型海报，或停下来让用户提供素材/配置图像生成服务
