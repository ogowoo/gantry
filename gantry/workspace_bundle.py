# -*- coding: utf-8 -*-
"""工作区打包注入：新任务首轮把工作区文本文件合并成一个 txt 附件给 Copilot。

用户需求（AUD-P5）：新会话时如果工作区有文件，打包成 txt 一次丢给 Copilot
分析，省掉模型逐个 read_text 的回合（省会话）；处理结果继续用 copilot_file
一次生成、从下载链接取回。

边界：
- 只收**文本类**文件（复用 fileview.TEXT_EXT 的扩展名表），二进制跳过；
- 逐文件上限、文件数上限、总字符上限全部可配且默认保守——打包是"加速上下文
  注入"，不是把整个仓库塞进对话；
- 目录剪枝与 @ 检索同一份噪声目录清单（node_modules/.venv/.git 等）。
"""
from __future__ import annotations

import os
from pathlib import Path

from .fileview import TEXT_EXT
from .sensitive_names import sensitive_name

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".idea",
              "data", ".spill", ".gantry-test-tmp", "EdgeProfile", "for debug"}
MAX_TOTAL_CHARS = 120_000
MAX_FILES = 40
MAX_PER_FILE = 60_000
# 大文件只给预览：完整内容进首轮消息会挤爆上下文，也与 spill 的"完整结果
# 不直接进提示词"哲学冲突（AUD-P5 审核 2026-11-26）。
INLINE_MAX_CHARS = 20_000


def build_workspace_bundle(root: str | Path, *, max_chars: int = MAX_TOTAL_CHARS,
                           max_files: int = MAX_FILES,
                           max_per_file: int = MAX_PER_FILE,
                           inline_max_chars: int = INLINE_MAX_CHARS) -> tuple[str, dict]:
    """把工作区文本文件合并成一个 txt。返回 (正文, 统计)。

    正文为空表示没有可打包的文件（或工作区不存在）。统计含
    {files, total_chars, truncated}——truncated=True 表示达到上限被截断，
    调用方应让模型知道"还有更多文件需要按需 read_text"。

    安全边界：
    - 凭据/密钥类文件名（.env、secrets、私钥、config.json 等，规则见
      sensitive_names）一律跳过——打包是自动全量外送，宁缺毋滥；
    - 超过 inline_max_chars 的文件只给头部预览 + "按需 read_text"提示，
      完整内容绝不进首轮消息（与 spill 同哲学）。
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return "", {"files": 0, "total_chars": 0, "truncated": False}
    parts: list[str] = []
    stats = {"files": 0, "total_chars": 0, "truncated": False,
             "previewed": 0, "skipped_sensitive": 0}
    try:
        walker = os.walk(root)
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in sorted(filenames):
                p = Path(dirpath) / name
                if p.suffix.lower() not in TEXT_EXT or name.startswith("."):
                    continue
                if sensitive_name(name):
                    stats["skipped_sensitive"] += 1
                    continue
                if stats["files"] >= max_files:
                    stats["truncated"] = True
                    return _finalize(parts, stats)
                try:
                    size = p.stat().st_size
                    if size > max_per_file * 4:          # 粗略字节上限（UTF-8 中文 3 字节）
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                full_len = len(text)
                if full_len > max_per_file:
                    text = text[:max_per_file] + "\n…（文件过长，已截断）"
                elif inline_max_chars > 0 and full_len > inline_max_chars:
                    # 大文件只给预览：完整内容让模型按需 read_text 去读
                    text = (text[:inline_max_chars]
                            + f"\n…（原文 {full_len} 字符，仅预览前 {inline_max_chars}；"
                              "需要全文时 read_text 按需读取）")
                    stats["previewed"] += 1
                header = f"### 文件: {p.relative_to(root).as_posix()}（{full_len} 字符）"
                block = header + "\n" + text.strip() + "\n"
                if stats["total_chars"] + len(block) > max_chars:
                    stats["truncated"] = True
                    return _finalize(parts, stats)
                parts.append(block)
                stats["files"] += 1
                stats["total_chars"] += len(block)
    except OSError:
        pass
    return _finalize(parts, stats)


def _finalize(parts: list[str], stats: dict) -> tuple[str, dict]:
    if not parts:
        return "", stats
    head = ("【工作区文件打包】（由 Gantry 自动生成；"
            "先基于这份内容了解现状，不必逐个 read_text。"
            "如需看未打包的文件再按需读取。）\n\n")
    body = head + "\n".join(parts)
    stats["total_chars"] = len(body)
    return body, stats


def workspace_bundle_text(root: str | Path | None, config: dict | None, *,
                          web_allowed: bool = True,
                          self_task: bool = False) -> str:
    """新任务首轮的工作区打包注入文本（含配置门控与截断说明）。

    返回空串表示不应注入：功能关闭（workspace_bundle=false 或上限为 0）、
    非 Copilot 网页通道、自升级任务、或工作区里没有可打包的文本文件。
    """
    cfg = config or {}
    if not web_allowed or self_task:
        return ""
    if str(cfg.get("workspace_bundle", True)).lower() in ("0", "false", "no", "off"):
        return ""
    if not root:
        return ""
    try:
        max_chars = max(0, int(cfg.get("workspace_bundle_max_chars", MAX_TOTAL_CHARS) or 0))
        max_files = max(0, int(cfg.get("workspace_bundle_max_files", MAX_FILES) or 0))
        max_per_file = max(0, int(cfg.get("workspace_bundle_max_per_file", MAX_PER_FILE) or 0))
        inline_max = int(cfg.get("workspace_bundle_inline_max_chars", INLINE_MAX_CHARS) or 0)
    except (TypeError, ValueError):
        max_chars, max_files, max_per_file = MAX_TOTAL_CHARS, MAX_FILES, MAX_PER_FILE
        inline_max = INLINE_MAX_CHARS
    if max_chars <= 0 or max_files <= 0 or max_per_file <= 0:
        return ""
    try:
        body, stats = build_workspace_bundle(root, max_chars=max_chars,
                                             max_files=max_files,
                                             max_per_file=max_per_file,
                                             inline_max_chars=inline_max)
    except Exception:                          # noqa: BLE001
        return ""
    if not body:
        return ""
    if stats.get("truncated"):
        body += ("\n\n【注意】以上打包因上限截断（已含 %d 个文件、%d 字符）；"
                 "工作区可能还有更多文件，需要时按需 read_text/list_directory 查看。"
                 % (stats.get("files", 0), stats.get("total_chars", 0)))
    if stats.get("previewed"):
        body += ("\n\n【注意】其中 %d 个大文件只给了头部预览，"
                 "需要全文时按需 read_text 读取。"
                 % stats.get("previewed", 0))
    if stats.get("skipped_sensitive"):
        body += ("\n\n【注意】已跳过 %d 个凭据类文件（.env/secrets/私钥等）——"
                 "它们不会自动打包外送，任务确实需要时请用户单独授权。"
                 % stats.get("skipped_sensitive", 0))
    return body
