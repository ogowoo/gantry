# 法律法规下载

`scripts/law_download.py` 使用国家法律法规数据库（flk.npc.gov.cn）的官方页面/接口，
不依赖模型生成的 PowerShell 命令。

## 运行

```powershell
# 先测一条：民法典
.\.venv\Scripts\python.exe scripts\law_download.py `
  --query 民法典 --limit 1 --out workspace\LAW --scope national --format pdf

# 国家层面全量（慢速，支持断点：已有文件/bbbs 会跳过）
.\.venv\Scripts\python.exe scripts\law_download.py `
  --limit 0 --pages 400 --page-size 100 --sleep 1.0 `
  --out workspace\LAW --scope national --format pdf

# 搜索深圳地方规定（按标题/制定机关包含“深圳”过滤）
.\.venv\Scripts\python.exe scripts\law_download.py `
  --query 深圳 --limit 5 --scope shenzhen --out workspace\LAW
```

输出：

```text
workspace/LAW/
├── laws/
│   ├── index.json
│   └── national/
│       ├── 中华人民共和国民法典_2020-05-28.pdf
│       └── 中华人民共和国民法典_2020-05-28.meta.json
```

有些法规只有 Word 附件（`ossPdfPath=None`），脚本会自动回退下载 `.docx`；
`meta.json` 的 `format` 会记录实际格式。

## 实现链路

```text
Playwright Chromium 打开 flk.npc.gov.cn
  → 页面上下文 fetch /law-search/search/list
  → 页面上下文 fetch /law-search/search/flfgDetails?bbbs=...
  → 页面上下文 fetch /law-search/download/pc?format=pdf&bbbs=...
  → 拿到官方 OSS 签名 URL
  → httpx 下载 PDF + 校验 %PDF-
  → 保存 meta.json（bbbs / 标题 / 公布日期 / 施行日期 / 来源 / OSS 路径）
```

为什么要经过浏览器：

- `/law-search/search/list` 和详情接口虽然返回 JSON，但直接 httpx 请求会被 WAF/JS 壳拦截；
- 浏览器完成站点 JS 校验后，页面内 `fetch` 才能拿到 JSON；
- 下载链接由官方接口生成签名 URL，脚本只下载这个受控 URL。

## 已验证

```text
中华人民共和国民法典
PDF 1,205,952 bytes
176 页
```

## 限制

- 不是每个法规都有 PDF 附件；脚本会跳过并打印原因。
- 批量抓取必须控制限速；不要并发轰炸站点。
- 深圳站点/地方规定可能使用不同的检索接口，`--scope shenzhen` 只是基于 FLK
  搜索结果中的“深圳”过滤，不等于深圳市政府所有规章。
- 站点改版后需要重新核对接口字段。
