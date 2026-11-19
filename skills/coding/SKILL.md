---
name: coding
description: 编程任务的执行规范：先读后改、最小改动、validate_python + run_unittest 闭环、交付可验证结果
---

# 编程任务

## 什么时候用

用户要求实现/修改/重构/修复代码、写脚本、补测试、分析项目代码时。

## 硬约束

1. **先读后改**：用 `find_files` / `read_text` 定位真实文件、函数和行号，再改；
   不要凭记忆重写整文件，不要凭猜测拼路径。
2. **最小改动**：优先 `apply_patch` / `replace_text` / `replace_lines`；
   不顺手重构、格式化、升级依赖或改无关文件。
3. **改完就验证**：
   - 每个改动的 Python 文件跑 `validate_python`；
   - 有 `tests/` 就跑 `run_unittest`；失败要修，不能返回 final 说“应该可以”。
4. **不要伪造验证**：没跑测试就明确说没跑；跑失败就报告原始错误。
5. 新增依赖必须先说明理由和安装方式；不要静默 `pip install`。

## 推荐流程

```text
find_files/read_text 定位
  → apply_patch 最小修改
  → validate_python
  → run_unittest（存在测试时）
  → 读取 diff/测试输出确认
  → 汇报：改了哪些文件、验证命令、结果、剩余风险
```

## 交付格式

```text
改动：path:line ...（为什么）
验证：validate_python / run_unittest 的命令与结果
风险：未覆盖的测试、未验证的运行路径
```

## 常见坑

- 任务文本里的路径相对**工作区**；不要改到服务安装目录（自升级任务除外，用 `self_*`）。
- 大文件只读目标段：`read_text(offset, limit)`；不要整篇打印。
- Windows 路径/编码：Python 文件显式 UTF-8；不要用 PowerShell 重写源码。
- 测试失败先看第一个 traceback，不要一次改十几个地方。
