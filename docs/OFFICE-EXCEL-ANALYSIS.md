# Office 工具全面分析与效率提升（2026-11-23）

对 `tools_office.py` / `excel_guard.py` / `excel_worker.py` 的完整体检：
功能漏洞、性能瓶颈、以及本轮已落地的修复。基准文件为 3.7MB 正常表
（50k 行 × 20 列，100 万单元格）与 5.4MB 不规范表（膨胀 dimension
XFD1048576、隐藏行、合并单元格、空行、样式格）。

## 一、性能结论（先回答"pandas 是不是更快"）

**pandas 本身不快，pandas+calamine 引擎才快。** pandas 读 xlsx 默认走
openpyxl 引擎，实测比裸 openpyxl 还慢（9.3s vs 7.9s，dtype=str 转换开销）。
真正的数量级差距来自 python-calamine（Rust 实现，未来 pandas 的默认引擎）：

| 读 50k×20 全表 | 耗时 |
|---|---|
| openpyxl read_only（改造前） | 7.9s / 不规范表 5.7s |
| pandas（openpyxl 引擎） | 9.3s / 6.6s |
| **python-calamine（本次采用）** | **~1.0s（含惰性解析），迭代 0.1s** |

## 二、本轮已落地的改动

### 1. 读取引擎：python-calamine（gantry/excel_worker.py）
- `read_range` / `read_workbook` 优先走 calamine，openpyxl 自动兜底；
  未装 python-calamine 的环境行为与之前完全一致。
- 输出语义对齐：`_cell_str` 把 calamine 的 float 整型化（2.0→"2"）、
  bool→"True"/"False"、None→""，与 openpyxl 路径逐格一致（有测试锁定）。
- **顺带解锁 .xls / .xlsb / .ods 读取**（calamine 原生支持）：
  `read_excel`/`excel_preview` 在 calamine 可用时不再拒绝 .xls；
  `excel_read_chunk` 因依赖 zip 结构探测仍只支持 xlsx/xlsm。
- pyproject.toml / requirements.txt 增加 `python-calamine>=0.3,<1`。

### 2. 常驻 worker 进程（gantry/tools_office.py `_PersistentExcelWorker`）
- **发现**：Windows 上 python 子进程 spawn 开销实测在 0.2~1.2s 间波动
  （杀软干扰），asyncio 子进程路径更糟；分块循环读时这是每块都付的税。
- **方案**：`python -m gantry.excel_worker serve` 常驻模式（stdin/stdout
  行协议），空闲 300s 自动回收（Job Object 内存上限随之释放）；
  请求串行加锁；写 stdin 用独立线程避免管道双向填满死锁；
  超时 watchdog 杀进程后自动重启重试一次，再死转 ToolError。
- 超时/内存保护不变：watchdog + Job Object 硬上限双重兜底。

### 3. worker 内物化行缓存（gantry/excel_worker.py `_ROWS_CACHE`）
- **发现**：calamine 的 `get_sheet_by_name` 每次都重新解析整个文件
  （缓存 workbook 对象没用），是大文件分块读的地板成本。
- **方案**：按 (路径, 大小, mtime_ns, 表名) 缓存 `to_python()` 物化行；
  单表 ≤120 万单元格、总量 ≤150 万（512MB Job 上限内），LRU 淘汰。
  分块循环读首个请求付一次解析，后续块纯切片。

### 4. 探测缓存（tools_office `_inspect_report`）
- excel_preview / excel_read_chunk / read_excel 每次调用都先全量探测
  （大表 1~2s）。现按文件指纹（含 mtime_ns）缓存，分块循环读不再重复探测。

### 5. excel_guard 本体提速（gantry/excel_guard.py）
- sharedStrings 从 DOM（ET.fromstring，3 倍内存峰值）改 iterparse 流式；
- 扫描只监听 end 事件，回调开销近减半；
- **预算口径修正**：max_cells 只计"有值/公式"单元格——原实现把样式空
  单元格也计入，不规范文件会迅速烧光预算导致真实范围测不出来
  （truncated + 置信度虚低）。

### 实测端到端（含子进程/缓存全链路）

| 场景 | 改造前 | 改造后 |
|---|---|---|
| read_excel 热调用（探测+读 2000 行） | ~1.2s | **0.03s** |
| excel_read_chunk 连续 20 块 | ~22s | **0.49s** |
| 冷调用（新文件首次） | ~3.5s | ~2.8s（地板=guard 探测 1.7s+解析 1s） |

## 三、补上的功能漏洞

1. **read_csv / write_csv 一直缺失**：agent.py 的只读意图分类里引用了
   `read_csv`，但工具从未实现。现补齐：自动编码探测（utf-8-sig →
   utf-8 → gb18030，兼容国内 Excel 导出的 GBK CSV）、行/列上限截断、
   append/overwrite、utf-8-sig 带 BOM 写出（Excel 双击不乱码）。
2. **read_pptx 漏表格和组合形状**：原实现只看 slide.shapes 一层的
   has_text_frame，图形框架里的表格（has_table）和组合形状内部全部丢失。
   现递归提取，每页新增 `tables` 字段（"|" 分隔的行文本）。
3. **.xls 直接报错**：见上文 calamine 解锁。

## 四、遗留问题（未改，按价值排序）

1. **合并单元格未做值填充**：合并区除左上角外读出空串，表头合并时
   影响 header 识别。calamine 暴露 `merged_cell_ranges`，可加可选
   fill_merged=true。
2. **公式只读缓存值**：data_only=True，从未被 Excel 打开过的文件公式
   无缓存值 → 空串。可考虑 excel_inspect 顺带返回公式文本（仅探测
   阶段，不放大读取）。
3. **guard 探测仍是纯 Python XML 解析**（1.7s/1M 单元格），是冷调用
   地板。方向：lxml 加速（2~3 倍）或用 calamine 元数据做快路径，
   但 guard 的异常检测（样式空格、公式空格、隐藏行列）依赖 XML 细节，
   需要保留纯 stdlib 兜底。可考虑 config 开关。
4. **日期/百分比格式**：读出的是序列化值（0.05 而非 "5%"），日期是
   datetime 字符串。写入侧 write_excel 也不处理日期类型。
   如需报表级保真，得引入 styles 解析（成本不低）。
5. **docx 写入只有纯段落**：无表格写入、无样式；read_docx 无标题层级。
   PDF 无表格提取（pypdf 的 extract_text 对多栏表格效果差，
   需 pdfplumber 级别的布局分析）。
6. **excel_validate 不缓存**（有意）：验证就是要重新读。
7. **xlsx_patch 快路径**（大文件 write_excel 直改 XML）未动，与本次
   读取链路无冲突。

## 五、测试

- 新增 `tests/test_office_tools.py`（8 个用例）：CSV 往返/追加/GBK 回退/
  截断、pptx 表格+组合形状提取、calamine 与 openpyxl 输出语义一致、
  探测缓存命中与文件改写失效。
- 既有 tests/test_excel_guard、test_xlsx_patch、test_excel_worker_limits、
  test_prompt_language 全绿；全量 1933 用例无回归
  （失败项与 origin/main 基线逐项一致，均为环境类遗留）。
