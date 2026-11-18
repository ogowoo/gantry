# -*- coding: utf-8 -*-
"""本地知识库命令行：建索引 / 搜一下 / 看规模。

    python scripts/kb.py index [--force]      # 增量重建（默认 kb 目录：data/kb）
    python scripts/kb.py search "VPN 连不上"   # 看命中与分数（调 kb_min_score 用）
    python scripts/kb.py search "上不了外网" --mode hybrid   # 向量+词面混合
    python scripts/kb.py embed                # 建/更新向量索引（需要 embedding 端点）
    python scripts/kb.py stats
    python scripts/kb.py sample               # 首次在 data/kb 放一篇格式样例（不覆盖已有文件）

知识库内容就是 `data/kb/**/*.md`：文件头可选 front-matter（title/tags），正文按
`#`/`##` 标题切块。写人话就行，不用管索引格式。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry.kb import KnowledgeBase  # noqa: E402

SAMPLE = """---
title: VPN 连不上怎么办
tags: vpn, 网络, 远程办公
category: network
---

# VPN 连不上怎么办

## 症状
客户端提示"无法建立隧道"，或者一直转圈、连接后没有网络。

## 排查步骤
1. 先确认家里宽带能打开任意门户网站（排除断网）。
2. 换手机热点试一次：热点能连、宽带不能 → 运营商/DNS 问题。
3. 客户端里删除旧配置，重新导入最新配置文件。
4. 网关地址 vpn.example.com，端口 443；防火墙需放行 UDP 443。
5. 仍然不行 → 联系 IT 服务台（分机 8000），说明报错原文。

## 常见误区
- 反复点"重连"没用，先看客户端日志里的报错码。
"""


def _emb_cfg() -> dict:
    """从 config.json 的 kb.embedding 读向量配置（缺省 auto + 本机 18085）。"""
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        return dict((cfg.get("kb") or {}).get("embedding") or {})
    except Exception:                            # noqa: BLE001
        return {}


def _index(kb):
    from gantry.kb_semantic import index_for

    return index_for(kb, {"backend": "auto", **_emb_cfg()})


def _search(kb, query: str, limit: int, mode: str):
    """mode=fts 走纯词面；vector/hybrid 需要向量索引（没有就退回 fts 并提示）。"""
    q = str(query or "").strip()
    if mode == "fts":
        return kb.search(q, limit)
    idx = _index(kb)
    if idx is None:
        print("（没有可用的 embedding 端点，已退回词面检索）", file=sys.stderr)
        return kb.search(q, limit)
    st = idx.stats()
    if st["vectorised"] == 0:
        print("（向量索引为空，正在建…）", file=sys.stderr)
        idx.build()
    return idx.search(q, limit) if mode == "vector" else idx.hybrid(q, limit)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="本地知识库工具")
    ap.add_argument("cmd", choices=("index", "search", "stats", "sample", "embed",
                                    "vector-stats"))
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--force", action="store_true", help="忽略 mtime 强制重建")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--mode", default="fts", choices=("fts", "vector", "hybrid"),
                    help="检索方式（search 用）")
    args = ap.parse_args(argv)

    kb = KnowledgeBase(ROOT)
    try:
        if args.cmd == "index":
            print(json.dumps(kb.reindex(force=args.force), ensure_ascii=False, indent=2))
        elif args.cmd == "search":
            hits = _search(kb, args.query, args.limit, args.mode)
            if not hits:
                print("（没有命中）")
            for h in hits:
                head = f"《{h['title']}》" + (f" › {h['heading']}" if h["heading"] else "")
                print(f"[{h['score']:.3f}] {head}  ({h['path']})")
                print("   " + h["text"].replace("\n", " ")[:160])
        elif args.cmd == "embed":
            idx = _index(kb)
            if idx is None:
                print("没有可用的 embedding 端点（检查 config.json 的 kb.embedding，"
                      "或把 backend 设成 hash 走零依赖兜底）")
                return 2
            print(json.dumps(idx.build(force=args.force), ensure_ascii=False, indent=2))
        elif args.cmd == "vector-stats":
            idx = _index(kb)
            if idx is None:
                print(json.dumps({"available": False}, ensure_ascii=False))
            else:
                print(json.dumps({"available": True, **idx.stats()}, ensure_ascii=False,
                                 indent=2))
        elif args.cmd == "stats":
            print(json.dumps(kb.stats(), ensure_ascii=False, indent=2))
        elif args.cmd == "sample":
            kb.dir.mkdir(parents=True, exist_ok=True)
            target = kb.dir / "vpn.md"
            if target.exists():
                print(f"已存在，不覆盖：{target}")
            else:
                target.write_text(SAMPLE, encoding="utf-8")
                print(f"已写入样例：{target}（改完记得 python scripts/kb.py index）")
            readme = kb.dir / "README.md"
            if not readme.exists():
                readme.write_text(
                    "# 本地知识库\n\n"
                    "把 IT 问答写成 `.md` 丢进这个目录即可（支持子目录）。\n\n"
                    "可选文件头：\n\n"
                    "```\n---\ntitle: 标题\ntags: 关键词1, 关键词2\n---\n```\n\n"
                    "正文按 `#`/`##` 切块，命中后回答里会带「《标题》 › 小节」的出处。\n\n"
                    "命令：`python scripts/kb.py index` / `search \"问题\"` / `stats`\n",
                    encoding="utf-8")
                print(f"已写入说明：{readme}")
    finally:
        kb.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
