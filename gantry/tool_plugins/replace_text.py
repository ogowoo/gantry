"""replace_text tool plugin (auto-migrated from tools_files.py)."""
from __future__ import annotations

import re
from typing import Any
from .. import editmatch
from ..tools import ToolError
from ._files_utils import diff_payload, hash_file, nearest_hint, require_read


async def replace_text(registry, path: str, old: str, new: str, count: int = 1) -> dict[str, Any]:
    if not old:
        raise ToolError("old 不能为空")
    if len(new) > registry.max_write:
        raise ToolError("替换内容超过限制")
    # count=0 表示"全部替换"：模型做"把制表符统一换成 4 空格"这类批量替换时，
    # 默认 count=1 只改第一处，于是它一遍遍改、一遍遍读——真实时间线里正是
    # 这样空转了几十步（用户："一直在重复替换空格/制表的动作"）。保留 1 的默认，
    # 但接受 0=全部，并在有残留时把数量明确回给模型。
    try:
        count = int(count)
    except (TypeError, ValueError):
        raise ToolError("count 必须是整数（0 = 全部替换）")
    count = 0 if count <= 0 else min(count, 100)
    target = registry._path(path)
    if not target.is_file():
        raise ToolError(f"文件不存在: {path}")
    require_read(registry, path)
    # 严格解码 + newline=""：非 UTF-8 文件拒绝替换（避免乱码写回），
    # CRLF 文件保持 CRLF，不做换行符静默转换。
    try:
        with target.open("r", encoding="utf-8", errors="strict", newline="") as f:
            text = f.read()
    except UnicodeDecodeError as exc:
        raise ToolError(f"文件不是有效的 UTF-8 文本，拒绝替换（防止乱码写回）: {exc}")
    found = text.count(old)
    old_used, new_used = old, new
    strategy = "exact"
    if found == 0:
        # 行尾容错：Windows 工作区文件是 CRLF，而模型从 JSON 里给出的多行
        # 片段通常是 LF（JSON 里 \n 更自然）。按文件自身的行尾风格改写片段
        # 再试一次——实测这是"未找到要替换的原文本"失败的主因（真实任务里
        # 一个会话失败 91 次）。只替换文件里真实存在的那个变体，不会改动
        # 文件其余部分的行尾风格。
        if "\r\n" in text and "\r\n" not in old:
            cand = old.replace("\n", "\r\n")
            if text.count(cand):
                old_used = cand
                new_used = new if "\r\n" in new else new.replace("\n", "\r\n")
                found = text.count(cand)
        elif "\r\n" not in text and "\r\n" in old:
            cand = old.replace("\r\n", "\n")
            if text.count(cand):
                old_used = cand
                new_used = new.replace("\r\n", "\n")
                found = text.count(cand)
    if found == 0:
        # 多级降级匹配（Codex/DSH 的编辑工具同样思路）：模型抄片段时最容易失真
        # 的是行首缩进与行尾空白；卡在"逐字相等"上会烧掉大量回合。
        m = editmatch.find_span(text, old)
        if m and m.unique:
            eol = editmatch._detect_eol(text)
            fixed_old = m.matched
            fixed_new = editmatch.reindent(new, m.matched, eol)
            if fixed_old and text.count(fixed_old):
                old_used, new_used = fixed_old, fixed_new
                found = text.count(fixed_old)
                strategy = m.strategy
        if found == 0:
            raise ToolError("未找到要替换的原文本，请先 read_text 后使用精确短片段"
                            + nearest_hint(text, old)
                            + "。" + editmatch.first_diff(text, old))
    replaced = found if count == 0 else min(found, count)
    after_text = (text.replace(old_used, new_used) if count == 0
                  else text.replace(old_used, new_used, count))
    before=hash_file(target)
    registry._snap("replace_text", path, target)
    with target.open("w", encoding="utf-8", newline="") as f:
        f.write(after_text)
    after=hash_file(target)
    _left = max(0, found - replaced)
    _out = {"path":path,"replaced":replaced,"remaining_matches":_left,"match":strategy,
            "file_event":{"tool":"replace_text","path":path,"before_hash":before,"after_hash":after,"result":"ok"}}
    _out.update(diff_payload(path, text, after_text))
    if strategy != "exact":
        _out["note"] = (f"片段与文件并非逐字相同（按 {strategy} 容错匹配，且已按文件缩进校正新内容）；"
                        "下次可直接复用文件里的原样片段")
    if _left:
        _out["hint"] = (f"还有 {_left} 处要替换：要一次全改请传 count=0（0=全部）；"
                        "改缩进/空白这类批量编辑更稳的是 convert_indentation 或 replace_lines")
    if before == after:
        _out["warning"] = ("内容没有变化：old 与 new 可能等价，或该片段被前导空白干扰没匹配上。"
                           "先 read_text(reveal_whitespace=true) 看清真实空白再改")
    return _out


def register(registry):
    async def _call(*args, **kwargs):
        return await replace_text(registry, *args, **kwargs)
    registry.register('replace_text', _call, '在现有文件中精确替换短文本，修改代码时优先使用', {"path": '相对路径', "old": '原文本', "new": '新文本', "count": '替换次数，默认1（0=全部）'})
