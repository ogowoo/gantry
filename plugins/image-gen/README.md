# image-gen

文生图插件。保留旧工具名 `image_generate`，内部改为 Provider 层：

```text
providers/
├── base.py        ImageRequest / GeneratedImage / 校验
├── openai.py      OpenAI Images
├── dashscope.py   通义万相异步任务
├── router.py      能力/质量/成本路由与回退
└── env.py         API Key 读取
```

## 工具

```text
image_generate(prompt, size="1024*1024", n=1, backend="", model="", negative_prompt="")
```

输出保存到当前工作区 `generated/`，返回：

```json
{
  "provider": "dashscope",
  "model": "wanx2.1-t2i-turbo",
  "files": ["generated/20261120-abc123-0.png"],
  "images": [{"path": "...", "width": 1024, "height": 1024, "bytes": 12345}],
  "urls": ["https://..."]
}
```

## 路由

`backend=auto` 时按 `auto_strategy` 排序：

- `balanced`（默认）：质量与成本综合
- `quality`：优先质量
- `cost`：优先成本

显式 `backend=dashscope|openai` 时只走该 Provider；`allow_fallback` 控制自动路由失败后的回退。

## 资产

生成图不会自动进入设计项目。配合 visual-design：

```text
image_generate(prompt="...")
asset_import(project_id="poster", source="generated/...png", role="background")
```

Provider 下载与解码都会校验 20 MB 上限，并由 Pillow 验证图片可读。

DashScope 额外支持 `negative_prompt`，用于压掉文字、水印和边框，例如：

```text
negative_prompt="文字，字母，数字，水印，logo，二维码，画框，白边"
```
