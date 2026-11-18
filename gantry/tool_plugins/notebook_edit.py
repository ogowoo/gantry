"""notebook_edit tool plugin: edit Jupyter notebook cells (Claude Code NotebookEdit)."""
from __future__ import annotations

import json
from typing import Any

from ..tools import ToolError
from ._files_utils import diff_payload, hash_file, require_read

_ALLOWED_TYPES = {"code", "markdown", "raw"}
_ALLOWED_ACTIONS = {"replace", "insert", "delete"}


def _source_lines(text: str) -> list[str]:
    """nbformat 约定：source 是行列表，行尾带 \n（最后一行可无）。"""
    raw = str(text or "")
    if not raw:
        return []
    lines = raw.splitlines(keepends=True)
    return lines if lines else [raw]


def _new_cell(cell_type: str, source: str) -> dict[str, Any]:
    cell: dict[str, Any] = {"cell_type": cell_type,
                            "metadata": {},
                            "source": _source_lines(source)}
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


async def notebook_edit(registry, path: str, cell_index: int, action: str = "replace",
                        cell_type: str = "code", source: str = "") -> dict[str, Any]:
    """按索引替换/插入/删除 notebook 单元格；整文件以 JSON 原子写回。"""
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    if target.suffix.lower() != ".ipynb":
        raise ToolError("notebook_edit 只支持 .ipynb 文件")
    require_read(registry, path)
    act = str(action or "replace").strip().lower()
    if act not in _ALLOWED_ACTIONS:
        raise ToolError("action 只能是 replace / insert / delete")
    ctype = str(cell_type or "code").strip().lower()
    if ctype not in _ALLOWED_TYPES:
        raise ToolError("cell_type 只能是 code / markdown / raw")
    try:
        idx = int(cell_index)
    except (TypeError, ValueError):
        raise ToolError("cell_index 必须是整数")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ToolError(f"不是有效的 notebook JSON: {exc}")
    cells = data.get("cells")
    if not isinstance(cells, list):
        raise ToolError("notebook 缺少 cells 数组")
    before_text = target.read_text(encoding="utf-8")

    if act == "delete":
        if idx < 0 or idx >= len(cells):
            raise ToolError(f"cell_index={idx} 越界（共 {len(cells)} 个单元格）")
        removed = cells.pop(idx)
        result_note = f"已删除单元格 {idx}（{removed.get('cell_type')}）"
    elif act == "insert":
        if idx < 0 or idx > len(cells):
            raise ToolError(f"cell_index={idx} 越界（可插入范围 0-{len(cells)}）")
        cells.insert(idx, _new_cell(ctype, source))
        result_note = f"已在位置 {idx} 插入 {ctype} 单元格"
    else:  # replace
        if idx < 0 or idx >= len(cells):
            raise ToolError(f"cell_index={idx} 越界（共 {len(cells)} 个单元格）")
        cells[idx] = _new_cell(ctype, source)
        result_note = f"已替换单元格 {idx} 为 {ctype}"

    new_text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    before = hash_file(target)
    registry._snap("notebook_edit", path, target)
    target.write_text(new_text, encoding="utf-8", newline="")
    after = hash_file(target)
    out: dict[str, Any] = {"path": path, "action": act, "cell_index": idx,
                           "cell_type": ctype, "cell_count": len(cells),
                           "changed": before != after, "note": result_note,
                           "file_event": {"tool": "notebook_edit", "path": path,
                                          "before_hash": before, "after_hash": after,
                                          "result": "ok"}}
    out.update(diff_payload(path, before_text, new_text))
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await notebook_edit(registry, *args, **kwargs)
    registry.register(
        "notebook_edit",
        _call,
        "编辑 Jupyter notebook（.ipynb）单元格：按索引 replace / insert / delete；先 read_text 或 notebook_read 再改",
        {"path": "相对路径（.ipynb）", "cell_index": "单元格下标（0 起）",
         "action": "replace / insert / delete，默认 replace",
         "cell_type": "code / markdown / raw，默认 code",
         "source": "新的单元格源码（replace/insert 时必填）"},
    )
