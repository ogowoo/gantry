# -*- coding: utf-8 -*-
"""Teams Web 的 Chat → Mentions 读取与点开。

实测 DOM（2026-11，teams.cloud.microsoft/v2）：
- 中间 Mentions 栏容器：`[data-tid="app-layout-area--sub-nav"]`
- 行：`[role="row"].fui-TreeGridRow`
- 行内：`message-slice-card-title`（发送人）、`message-slice-card-preview`（消息）、
  `message-slice-card-timestamp`（时间）。

Mentions 是全局“@我”流，比 Activity 更精确；点行会直接打开对应会话，
所以用它做 `source: "mentions"` 的读侧，不依赖 IDB 的聊天列表排序。
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from . import bridge as _b
from .teams_common import _norm_identity, acquire_page_lock, release_page_lock
from .teams_dom import header_title, teams_open
from .teams_logic import strip_leading_name

_PANE = '[data-tid="app-layout-area--sub-nav"]'
_PREVIEW = '[data-tid="message-slice-card-preview"]'


def badge_from_text(text: str) -> int:
    """从 "Mentions\\n1" 里取未读数；没有数字返回 0。"""
    m = re.search(r"(\d+)\s*$", str(text or "").strip())
    return int(m.group(1)) if m else 0


def normalise_rows(rows: list[dict[str, Any]], my_name: str = "") -> list[dict[str, Any]]:
    """补 question（剥掉开头的自己的名字）和稳定 key。"""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows or []:
        msg = str(r.get("message") or "").strip()
        if not msg:
            continue
        sender = str(r.get("sender") or "").strip()
        t = str(r.get("time") or "").strip()
        q = strip_leading_name(msg, my_name) if my_name else msg
        q = q or msg
        key = f"{sender}|{t}|{q}"
        if key in seen:
            continue
        seen.add(key)
        out.append({**r, "sender": sender, "message": msg,
                    "question": q, "time": t, "key": key})
    return out


async def badge_count(page=None) -> int:
    page = page or await teams_open()
    text = await page.evaluate("""() => {
      const el = [...document.querySelectorAll('[role="treeitem"]')]
        .find(e => (e.innerText || '').trim().startsWith('Mentions'));
      return el ? (el.innerText || '').trim() : '';
    }""")
    return badge_from_text(text)


async def _mentions_open(page) -> bool:
    """确保中间栏是 Mentions；不是则点 Quick views → Mentions（click + Enter）。"""
    has = await page.evaluate(
        """(sel) => !!document.querySelector(sel)""", _PANE + " " + _PREVIEW)
    if has:
        return True
    rect = await page.evaluate("""() => {
      const el = [...document.querySelectorAll('[role="treeitem"]')]
        .find(e => (e.innerText || '').trim().startsWith('Mentions'));
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {x: r.x, y: r.y, w: r.width, h: r.height};
    }""")
    if not rect:
        return False
    try:
        await page.mouse.click(rect["x"] + rect["w"] * 0.35,
                               rect["y"] + rect["h"] / 2)
        await asyncio.sleep(0.6)
        await page.keyboard.press("Enter")
    except Exception as exc:                     # noqa: BLE001
        _b.log(f"Mentions 打开失败: {str(exc)[:120]}")
        return False
    for _ in range(12):
        await asyncio.sleep(0.5)
        has = await page.evaluate(
            """(sel) => !!document.querySelector(sel)""", _PANE + " " + _PREVIEW)
        if has:
            return True
    return False


async def read_mentions(page=None, my_name: str = "", gate: bool = True) -> dict[str, Any]:
    """读取 Mentions 列表；gate=True 时先看左侧 Mentions 未读 badge。"""
    await acquire_page_lock()
    try:
        page = page or await teams_open()
        if gate:
            try:
                n = await badge_count(page)
            except Exception:                    # noqa: BLE001
                n = 0
            if n <= 0:
                return {"ok": True, "badge": 0, "opened": False, "rows": []}
        opened = await _mentions_open(page)
        if not opened:
            return {"ok": False, "badge": None, "opened": False,
                    "rows": [], "error": "打不开 Mentions 视图"}
        rows = await page.evaluate("""() => {
          const pane = document.querySelector('[data-tid="app-layout-area--sub-nav"]');
          if (!pane) return [];
          const out = [];
          const seen = new Set();
          for (const row of pane.querySelectorAll('[role="row"]')) {
            const preview = row.querySelector('[data-tid="message-slice-card-preview"]');
            if (!preview) continue;
            const title = row.querySelector('[data-tid="message-slice-card-title"]');
            const ts = row.querySelector('[data-tid="message-slice-card-timestamp"]');
            const msg = (preview.innerText || '').trim();
            if (!msg) continue;
            const sender = ((title ? title.innerText : '') || '')
              .trim().split('\\n')[0].trim();
            const time = ((ts ? ts.innerText : '') || '').trim();
            const key = sender + '|' + msg + '|' + time;
            if (seen.has(key)) continue;
            seen.add(key);
            const r = row.getBoundingClientRect();
            out.push({sender, message: msg, time, key,
                      raw: (row.innerText || '').slice(0, 300),
                      x: r.x, y: r.y, w: r.width, h: r.height});
          }
          return out;
        }""")
        return {"ok": True, "badge": None, "opened": True,
                "rows": normalise_rows(rows, my_name)}
    finally:
        release_page_lock()


async def open_mention(row: dict[str, Any], page=None,
                       my_name: str = "") -> dict[str, Any]:
    """点开一条 mention 行，返回当前会话头部标题（即聊天名）。"""
    await acquire_page_lock()
    try:
        page = page or await teams_open()
        key = str(row.get("key") or "")
        rows = await page.evaluate("""() => {
          const pane = document.querySelector('[data-tid="app-layout-area--sub-nav"]');
          if (!pane) return [];
          const out = [];
          for (const row of pane.querySelectorAll('[role="row"]')) {
            const preview = row.querySelector('[data-tid="message-slice-card-preview"]');
            if (!preview) continue;
            const title = row.querySelector('[data-tid="message-slice-card-title"]');
            const ts = row.querySelector('[data-tid="message-slice-card-timestamp"]');
            const msg = (preview.innerText || '').trim();
            const sender = ((title ? title.innerText : '') || '')
              .trim().split('\\n')[0].trim();
            const time = ((ts ? ts.innerText : '') || '').trim();
            const r = row.getBoundingClientRect();
            out.push({sender, message: msg, time,
                      x: r.x, y: r.y, w: r.width, h: r.height});
          }
          return out;
        }""")
        hit = None
        for r in rows:
            q = strip_leading_name(r.get("message") or "", my_name) if my_name \
                else (r.get("message") or "")
            k = f"{r.get('sender')}|{r.get('time')}|{q or r.get('message')}"
            if k == key:
                hit = r
                break
        if not hit:
            return {"opened": False, "error": "mention 行已消失（可能已被读过）"}
        if hit["y"] < 0 or hit["y"] > 900:
            return {"opened": False, "error": "mention 行不在可视区"}
        await page.mouse.click(hit["x"] + hit["w"] * 0.5,
                               hit["y"] + hit["h"] * 0.5)
        await asyncio.sleep(2.5)
        head = await header_title(page)
        if not head:
            return {"opened": False, "error": "点了 mention 但没读到会话标题"}
        # header_title 可能带邮箱/“Chat”/“Has context menu”，只取第一行做聊天名
        lines = [x.strip() for x in head.splitlines() if x.strip()]
        chat = lines[0] if lines else head.strip()
        return {"opened": True, "chat": chat, "head": head, "key": key}
    except Exception as exc:                     # noqa: BLE001
        return {"opened": False, "error": str(exc)[:160]}
    finally:
        release_page_lock()
