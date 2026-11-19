---
name: office365
description: Excel / Word / PPT / PDF / Outlook 的本地处理规范：保留格式、读后写、重开验证、发信确认
---

# Office 365 任务

## 什么时候用

用户要求处理 Excel、Word、PowerPoint、PDF、Outlook 邮件、报表，或把数据导出成 Office 文件时。

## 工具

| 工具 | 用途 |
|---|---|
| `read_excel` / `write_excel` | 读取/写入工作表（openpyxl） |
| `read_docx` / `write_docx` | 读取/生成 Word（python-docx） |
| `read_pptx` | 提取 PPT 文本/备注 |
| `read_pdf` | 提取 PDF 文本 |
| `outlook_unread` / `outlook_read` / `outlook_search` | OWA 邮件读取 |
| `outlook_send` | 发信（高风险，必须确认） |
| `run_python` | 工具覆盖不到的批量转换/计算 |

## 硬约束

1. **先读原文件**再修改；不要凭标题猜结构。
2. **大文件批量写**：`write_excel` 一次传完整二维数组，不要一行调一次。
   超过 1 MB 的 `.xlsx/.xlsm` 自动走 XML 快路径（保留样式/公式/条件格式）。
3. **保留格式**：公式、样式、合并单元格、页眉页脚、表格结构不能顺手改掉。
4. **写完重开验证**：`write_*` 后用 `read_*` 读回目标区域，确认值/行数/工作表存在。
5. **不编数据**：缺失值标注“未提供”，不要用猜测填充。
6. **发信必须确认**收件人、主题、正文和附件；只在用户明确要求时调用 `outlook_send`。
7. 交付时给文件相对路径、工作表/页范围、修改摘要。

## 推荐流程

```text
read_excel / read_docx / read_pdf 看结构
  → 明确目标单元格/段落/页
  → write_excel / write_docx（保留原格式）
  → read_* 验证
  → 汇报：路径、范围、改了什么、未处理项
```

## 注意

- 扫描件 PDF 没有文本层时先说明，不要假装读到了内容。
- Excel 公式未计算时读取可能是公式字符串；需要计算值就用 `run_python` + 明确依赖。
- Outlook 默认 OWA 自动化，依赖已登录的 EdgeProfile；没有登录时先让用户完成登录。
