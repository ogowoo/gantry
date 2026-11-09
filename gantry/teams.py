"""Teams 网页版自动化后端（类比 `owa.py`，复用桥接的浏览器与 SSO 登录态）。

## 为什么是网页自动化

Teams 机器人正规做法是 Graph/Bot Framework，但两者都要**管理员授权**（`Chat.Read`、
Bot 注册、公网回调）。这里要的是"同租户、本机、零授权"：直接驱动已经登录的 Teams 网页。

## 坑与对策

1. **纯 HTTP 会被判 unsupported-browser**（实测 `teams.microsoft.com/v2/` 直接跳到
   `/v2/unsupported-browser`），所以必须走真 Chromium（Playwright）——同一套 ctx 就行。
2. **DOM 是动态 + 虚拟列表**：选择器会随版本变。这里每个位置都给一串候选（`_SEL`），
   取第一个可见的；`probe()` 会把"到底命中哪些、当前页面长什么样"dump 出来，
   换版本时先跑 probe 再改选择器，别猜。
3. **消息身份**：Teams 没暴露稳定的 message id（v2 是 React + 虚拟列表），
   所以用 `(作者, 文本, 相对顺序)` 的哈希做去重键；调用方（插件）自己持久化 seen 集合。
4. **只读优先**：`send_message()` 需要显式调用；插件默认 `dry_run`。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as _html
import json
import re
import time
from pathlib import Path
from typing import Any

from . import bridge as _b
from .bridge import diagnostics
from .teams_dom import (  # noqa: F401
    _GANTRY_TAB_MARKERS, _SEL, teams_page,
)
from .teams_api import (  # noqa: F401
    _CAPTURED, _PATHS, _SW2, _SW_LAST,
)
from .teams_dom import (  # noqa: F401  （对外名字保持不变）
    api_search_typing,
    _ME_PREFIX,
    _msg_key,
    _pane_fingerprint,
    header_title,
    _real_click,
    recent_messages,
    dom_summary,
    _first_visible,
    _visible_count,
    _clean_me,
    signed_in_name,
    _settle,
    _warm_ready,
    _goto_chat_home,
    _existing_teams_page,
    teams_tabs,
    close_extra_gantry_tabs,
    open_teams_for_login, teams_open,
    chat_participants,
    chat_kind,
    list_chats,
    open_chat_by_row,
    open_chat_by_thread_id,
    open_chat,
)
from .teams_mentions import (  # noqa: F401
    badge_count, normalise_rows, open_mention, read_mentions,
)
from .teams_send import (  # noqa: F401  （对外名字保持不变）
    compose_text,
    _compose_text_unlocked,
    send_message,
    _send_message_unlocked,
    _leave_chat,
    _title_matches,
    reply_to,
    _reply_to_unlocked,
    api_open_by_snippet,
    api_search_rows,
    api_search_probe,
    api_open_by_search,
    api_open_by_url,
    api_item_attrs,
    api_click_test,
    _down_up,
)
from .teams_api import (  # noqa: F401  （对外名字保持不变）
    _HOOK_JS,
    API_AUTHZ,
    API_CHATSVC,
    AUTHZ_TYPES,
    HOST_CANDIDATES,
    _PATHS,
    _jwt_claims,
    _claims_summary,
    _python_replay_authz,
    _python_chatsvc_probe,
    truncated,
    _ensure_skype_token,
    _resolve_conversation_id,
    api_send_message,
    _api_send_message,
    api_loki_probe,
    api_chain_probe,
    api_endpoints,
    api_page_mimic,
    api_mimic_probe,
    api_authz_types,
    api_replay_authz,
    api_captured,
    api_scroll_capture,
    api_try_headers,
    api_tokens_all,
    api_hosts_probe,
    api_decode_tokens,
    api_typing_capture,
    api_sw_capture2,
    api_sw_capture,
    api_cookie_probe,
    api_gtm_hosts,
    api_hook_capture,
    api_cdp_capture,
    api_capture,
    api_shape_probe,
    api_authcache_probe,
    api_deep_probe,
    api_storage,
    api_token,
    api_conversations,
    api_messages,
    api_probe,
    probe,
    probe_json,
)
from .teams_idb import (  # noqa: F401  （对外名字保持不变）
    api_idb_conversations, api_idb_enumerate, api_idb_probe, api_idb_record,
    api_idb_watch,
)
from .teams_common import (  # noqa: F401
    TeamsAuthRequired, TeamsBusyError, TeamsError,

    PAGE_LOCK_TIMEOUT, REPLY_TIMEOUT, TEAMS_URL, _PAGE_LOCK,
    _norm_identity, _plain_text, acquire_page_lock, page_lock_stats,
    release_page_lock, verify_conversation_identity,
)


def __getattr__(name):                       # ctx / CFG 惰性取（headless 切换会重建上下文）
    if name in ("ctx", "CFG"):
        return getattr(_b, name)
    raise AttributeError(name)


# 用户指定：新 Teams 客户端在 **teams.cloud.microsoft**（teams.microsoft.com 会跳转到同一个
# v2 客户端；纯 HTTP 访问会被判 unsupported-browser，必须走 Playwright 的真 Chromium）。
# 各租户策略不同，可在 config.json 的 teams_url 覆盖。
TEAMS_URL = str((_b.CFG.get("teams_url") or "https://teams.cloud.microsoft/v2"))

# 每个位置多个候选：从最具体到最宽松，命中第一个**可见**的


# 所有会改共享 Teams 页面的操作（打开会话/输入/发送/回退 UI）都串行化。
# 读 IndexedDB 可以并发；一旦要动页面，就必须拿这把锁。


# ── 网页客户端内部接口（只读部分）──────────────────────────────────────────


# 前缀可能带也可能不带 "for"/"of"（实测两种都出现过：
#   "Profile picture of Alex Chen - Example" / "Account manager for Justin Jia"）


