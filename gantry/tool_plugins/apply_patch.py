"""apply_patch tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

from typing import Any
from .. import editmatch
from ..tools import ToolError
from ._files_utils import diff_payload, hash_file, require_read


async def apply_patch(registry, path: str, patch: str = "", edits: Any = None,
                      count: int = 1) -> dict[str, Any]:
    """一次调用完成多处修改（**原子**：任一失败则整体不写）。

    两种输入：
    - `patch`：Codex 风格补丁，`*** Begin Patch` / `*** Update File: x` / `@@` 分块，
      每块用上下文行定位（` ` 上下文、`-` 旧行、`+` 新行）
    - `edits`：[{old, new, count?}] 简单替换列表

    为什么需要：逐条 `replace_text` 要在模型与文件之间来回 N 次，任一条因空白
    对不上就整轮重来——真实任务里这是回合与 token 爆掉的主因。补丁语义让
    "多处小改"变成一次调用、一次写入、一次验证。
    """
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}（apply_patch 只改现有文件；新建请用 write_text）")
    require_read(registry, path)
    try:
        with target.open("r", encoding="utf-8", errors="strict", newline="") as f:
            text = f.read()
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本，拒绝改写（防止乱码写回）: {exc}")
    eol = editmatch._detect_eol(text)
    try:
        batch = min(int(count), 100) if int(count) > 0 else 0
    except (TypeError, ValueError):
        batch = 1

    plan: list[tuple[str, str, int]] = []
    if isinstance(edits, list) and edits:
        plan = editmatch.parse_edits(edits)
        if not plan:
            raise ToolError("edits 为空或格式不对（每项需 {old, new}）")
    elif str(patch or "").strip():
        for idx, hunk in enumerate(editmatch.parse_patch(patch), 1):
            plan.append((eol.join(hunk.old_lines), eol.join(hunk.new_lines), 1))
        if not plan:
            raise ToolError("补丁里没有可应用的块（需要 `*** Update File:` 与 `@@` 分块）")
    else:
        raise ToolError("需要 patch 或 edits 之一")

    applied: list[dict[str, Any]] = []
    current = text
    for idx, (old, new, cnt) in enumerate(plan, 1):
        if not old.strip():
            # 纯插入块：定位不到上下文就拒绝，避免乱插入
            raise ToolError(f"第 {idx} 块缺少可定位的原文（补丁里要有上下文行或 - 行）")
        m = editmatch.find_span(current, old)
        if not m:
            raise ToolError(
                f"第 {idx} 块没有匹配上（已应用 {len(applied)} 块，**本次未写入任何改动**）。"
                + editmatch.first_diff(current, old))
        if not m.unique:
            raise ToolError(
                f"第 {idx} 块在文件中出现 {m.count} 次，无法唯一定位：请把上下文行写长一些"
                f"（本次未写入任何改动）")
        fixed_new = new
        if m.strategy != editmatch.STRATEGY_EXACT:
            # 容错匹配：新片段按文件实际缩进重排（tab 文件别被写成空格）
            fixed_new = editmatch.reindent(new, m.matched, eol)
        if cnt == 0:
            current = current.replace(m.matched, fixed_new)
        else:
            current = current[:m.start] + fixed_new + current[m.end:]
        applied.append({"index": idx, "match": m.strategy,
                        "line": current[:m.start].count(eol) + 1})

    if current == text:
        return {"path": path, "changed": False, "applied": applied,
                "note": "应用后内容没有变化（补丁的 new 与 old 等价）"}
    before = hash_file(target)
    registry._snap("apply_patch", path, target)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write(current)
    after = hash_file(target)
    fuzzy = [a["index"] for a in applied if a["match"] != editmatch.STRATEGY_EXACT]
    out: dict[str, Any] = {
        "path": path, "changed": before != after, "blocks": len(applied),
        "applied": applied, "fuzzy_blocks": fuzzy,
        "bytes": len(current.encode("utf-8")),
        "file_event": {"tool": "apply_patch", "path": path,
                       "before_hash": before, "after_hash": after, "result": "ok"},
    }
    out.update(diff_payload(path, text, current))
    if fuzzy:
        out["note"] = (f"有 {len(fuzzy)} 块是容错匹配（空白/缩进差异），已按文件缩进校正；"
                       "后续可用 read_text 复核")
    return out


def register(registry):
    async def _call(*args, **kwargs):
        return await apply_patch(registry, *args, **kwargs)
    registry.register('apply_patch', _call, '一次调用完成多处修改（原子：任一失败则不写入）；支持补丁或 edits 列表，四处以上小改用它省回合', {"path": '相对路径', "patch": "补丁文本：*** Begin Patch / *** Update File: x / @@ 分块，行首 ' '=上下文 '-'=旧 '+'=新", "edits": '或改用数组 [{old,new,count?}]（与 patch 二选一）', "count": '每条替换次数，默认 1（0=全部）'})
