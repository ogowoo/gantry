# Excel 大文件性能

日期：2026-11-19

## 问题

`openpyxl.load_workbook()`（普通模式）会按工作表 `<dimension>` 建满单元格对象。

实测用户 Tracker：

```text
文件：3.44 MB
实际内容：约 5,379 行 × 103 列
dimension：A1:XEJ5379
一次普通 load：约 15 秒
```

几个远端空样式单元格（`XEJ4153` 等）把 dimension 撑到 16,364 列，openpyxl 会按
约 8800 万格处理。`write_excel` 每调用一次都 load + save，所以写一个单元格也会卡
十几秒，并且会丢失它不支持的 Excel 条件格式扩展。

## 现在的处理

### 读

`read_excel`：

- 仍然用 `read_only=True, data_only=True`
- 只读 `max_rows`（默认 200，上限 2000）行，不再为了精确 `total_rows` 扫完整表
- `total_rows` 用 `ws.max_row` 元数据（尾部纯格式行可能偏大）
- 整个操作放到 `asyncio.to_thread`，不阻塞 WebUI/任务事件循环

### 写

`write_excel`：

- 文件 ≥ 1 MB 的 `.xlsx/.xlsm` 优先走 **XML 快路径**
- 直接改目标 worksheet XML，保留：
  - 单元格样式
  - 公式
  - 条件格式（含 x14 扩展）
  - 数据验证
  - 合并单元格
  - VBA（xlsm 原样保留）
- 失败/日期时间等复杂值自动回退 openpyxl
- 写入结果多一个字段：

```json
{"engine": "xml-fast"}
```

回退时：

```json
{"engine": "openpyxl"}
```

- 整个操作放到 `asyncio.to_thread`

实测同文件写 4 个单元格：

```text
openpyxl：约 15 秒
XML 快路径：约 0.5 秒
```

## 使用建议

- 大文件不要逐行调用 `write_excel`，一次传完整二维数组：

```python
rows = [["A", 1], ["B", 2]]
await tools.write_excel("tracker.xlsx", rows, "Sheet1", "A5400")
```

- 读大文件先 `max_rows`，需要更多再分页；不要一次读全表。
- 需要日期/时间格式的新单元格时仍走 openpyxl（XML 路径会回退），并保留原有样式。
- 极小文件仍走 openpyxl，行为与之前一致。

## 验证

```text
tests/test_excel_fast.py
```

测试覆盖：

- 大文件读只读 3 行
- 大文件写入走 `xml-fast`，重新打开值正确
- 小文件回退 openpyxl
- Tracker 条件格式/数据验证数量在 XML 写入后保持不变（手工验证）
