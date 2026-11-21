# Excel Data Guard

## 背景

Gantry 处理不规范 Excel 时，`max_row` / `max_column` / UsedRange 可能被远端格式、
残留样式或空公式撑到上百万行。旧 `read_excel` 虽然限制了 `max_rows` / `max_cols`，
但仍把 `max_row` 当 `total_rows`，并依赖 openpyxl 在超大 dimension 上迭代，存在
“看起来有百万行、实际只有几百行”的误判和卡死风险。

**核心原则：Excel 声明的范围不等于真实数据范围。**

## 审计结果

现有代码：

- `gantry/tools_office.py::read_excel` 使用 `openpyxl.load_workbook(read_only=True)`，
  通过 `max_rows`/`max_cols` 截断，但仍读取 `ws.max_row` / `ws.max_column` 作为总数；
- `read_excel` 用 `asyncio.to_thread` 避免阻塞事件循环，但线程本身没有硬资源预算；
- `kb_ingest.py` 的 xlsx 导入直接逐行读取，依赖 openpyxl 的迭代；
- `write_excel` 使用 XML 快路径 / openpyxl，不在本次读取保护范围内；
- 没有统一的 Excel 范围探测、异常报告或处理结果验证工具。

## 新增能力

### `excel_inspect`

直接流式解析 xlsx 的 sheet XML，不实例化百万单元格。返回：

```json
{
  "sheet": "Data",
  "declared_range": "A1:XFD1048576",
  "detected_range": "A1:J500",
  "actual_rows": 500,
  "actual_columns": 10,
  "anomalies": ["used_range_inflated", "declared_huge"],
  "confidence": 0.94,
  "header_sample": ["col1", "col2"],
  "truncated": false
}
```

检查项：

- 真实非空单元格 / 有效行 / 有效列；
- UsedRange 膨胀、声明超大范围；
- 表头识别与 `data_start_row`；
- 连续数据区域（`regions`），不连续区域标记 `multiple_regions`；
- 空公式、合并单元格、隐藏行列、远端格式残留；
- 数据空洞、稀疏列；
- 扫描预算（`max_cells` / `max_rows` / `max_seconds`）与 `truncated`；
- `needs_confirmation`：低置信度或多区域时建议先向用户确认。

### `excel_preview`

预览表头、前几行、探测范围和异常，不读取整表。

### `excel_read_chunk`

按 `chunk_rows` 分块读取，并把 `next_row/processed` 写入检查点：

```text
.gantry/excel_checkpoints/<sha1>.json
```

`resume=true` 从上次位置继续；`reset=true` 从头开始；`region` 可选择数据区域。
这样大表处理可以中断后续跑，而不是一次加载全部行。

### `read_excel(safe=true)`

先探测，再用 `detected_range` 读取：

- `total_rows` 使用真实行数，不再使用超大 `max_row`；
- 返回 `declared_range`、`detected_range`、`anomalies`、`confidence`；
- 探测失败时回退原有有界读取，不会静默丢失文件。

### `excel_validate`

重新打开输出文件，检查：

- 文件可读；
- 探测范围有效；
- 实际行数是否达到 `expect_rows`。

### Agent 行为

当 `read_excel` 可用时，系统提示词注入 Excel 规则：

```text
先 excel_inspect → 看 anomalies/confidence → 使用 detected_range
→ read_excel(safe=true) 分块处理 → excel_validate
```

低置信度或异常时先报告，不盲目扫描整表。

## 边界

- 目前结构探测只支持 `.xlsx` / `.xlsm`；旧 `.xls` 返回明确错误；
- XML 探测返回的是底层值（日期序列、公式缓存值可能保持原始形态），复杂格式/日期
  仍由 openpyxl 读取；
- 探测有预算，超限会返回 `scan_truncated` 和较低置信度，不会假装完整；
- 分块读取有检查点，但没有进程级内存上限；超大真实数据仍建议 chunk；
- 不自动修改原始工作簿；
- 检查点按“文件路径 + sheet + 文件大小/mtime”隔离，不跨文件复用。

## 测试

```powershell
.\.venv\Scripts\python.exe -W ignore -m unittest tests.test_excel_guard tests.test_excel_fast
```

覆盖：

- 实际 500 行 / 10 列、声明 XFD1048576；
- 正常文件不误报；
- 空 Sheet、多 Sheet；
- 安全读取使用 detected_range；
- `excel_validate` 行数检查；
- 工具 schema。
