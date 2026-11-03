"""Teams 机器人的**纯逻辑**（不碰浏览器、不碰网络）。

抽出来的原因：选择器/DOM 只能在真机校准，而"@ 识别、问题抽取、去重键、知识库回答拼装"
是最容易悄悄回归、也最容易离线验证的部分。插件（`plugins/teams-bot/plugin.py`）只负责
调度与 IO，判断都走这里，测试也就好写。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# 全角 ＠ 和半角 @ 都算；中英文冒号后常跟的空格也归一
_AT = r"[@＠]"
_WS = re.compile(r"[ \t\u3000]+")


def normalise_text(text: str) -> str:
    """压空白：行内多空格合一，最多保留一个空行（Teams 气泡里有大量注入的空格）。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if blank:
                continue
            blank = True
            out.append("")
        else:
            blank = False
            out.append(ln)
    return "\n".join(out).strip()


def has_mention(text: str, tokens: Iterable[str]) -> bool:
    """消息里是否出现了配置的 @ 标记（大小写不敏感，全角/半角 @ 都认）。"""
    body = (text or "").lower()
    for tok in tokens or []:
        tok = str(tok or "").strip()
        if not tok:
            continue
        t = tok.lower()
        if t.startswith("@") or t.startswith("＠"):
            t = t[1:]
        if not t:
            continue
        if re.search(_AT + r"\s*" + re.escape(t), body):
            return True
    return False


def mention_names(mentions: Iterable[Any]) -> list[str]:
    """从 `lastMessage.properties.mentions` 里取被 @ 的显示名。

    Teams 的 @ 不一定以 `@名字` 出现在正文里：常见是正文纯文本 +
    `properties.mentions` 元数据（`mentionText` / `mentioned.user.displayName`）。
    只靠正文找 `@` 会漏掉真实 @。
    """
    out: list[str] = []
    for m in mentions or []:
        if isinstance(m, str):
            out.append(m.strip())
            continue
        if not isinstance(m, dict):
            continue
        picked = ""
        for key in ("mentionText", "displayName", "displayname", "name"):
            v = m.get(key)
            if isinstance(v, str) and v.strip():
                picked = v.strip()
                break
        if not picked:
            mentioned = m.get("mentioned")
            if isinstance(mentioned, dict):
                user = mentioned.get("user") or {}
                if isinstance(user, dict):
                    v = user.get("displayName")
                    if isinstance(v, str) and v.strip():
                        picked = v.strip()
        if picked:
            out.append(picked)
    return list(dict.fromkeys(out))


def strip_mentions(text: str, names: Iterable[str]) -> str:
    """把被 @ 的显示名从正文里去掉（正文里的 @ 已被 Teams 拆成纯文本）。"""
    body = normalise_text(text)
    for name in sorted({str(n or "").strip() for n in (names or [])}, key=len, reverse=True):
        if name:
            body = body.replace(name, " ")
    return normalise_text(body)

def extract_question(text: str, tokens: Iterable[str]) -> str:
    """把 @ 标记剥掉，剩下的就是问题本体（可能为空 → 调用方忽略这条）。"""
    body = normalise_text(text)
    for tok in sorted({str(t or "").strip() for t in (tokens or [])}, key=len, reverse=True):
        if not tok:
            continue
        name = tok[1:] if tok[:1] in ("@", "＠") else tok
        if not name:
            continue
        body = re.sub(_AT + r"\s*" + re.escape(name), " ", body, flags=re.I)
    return normalise_text(body)


def is_new(key: str, seen: Iterable[str]) -> bool:
    """去重键是否没出现过（seen 可以是 list/set/deque）。"""
    return bool(key) and key not in set(seen or ())


def msg_key(author: str, text: str, ordinal: int) -> str:
    """消息身份：Teams 网页没给稳定 id，只能用 (作者, 文本, 页内序号) 的哈希。

    风险：如果两条同作者同文本的消息在列表里位置互换，键会变（罕见）；
    反过来，同一条消息在重新渲染后位置不变，键就稳定——实测够用。
    """
    return hashlib.sha1(f"{author}|{text}|{ordinal}".encode()).hexdigest()[:16]


def pick_hits(hits: list[dict[str, Any]], min_score: float = 0.35) -> list[dict[str, Any]]:
    """按阈值筛知识库命中（bm25 转成的分数，0~1）。"""
    out = [h for h in (hits or []) if float(h.get("score") or 0) >= float(min_score)]
    out.sort(key=lambda h: -float(h.get("score") or 0))
    return out


def build_kb_answer(question: str, hits: list[dict[str, Any]],
                    max_chars: int = 1200) -> str:
    """把知识库命中拼成一条**可溯源**的回复（不带模型，快且不会编）。

    格式：
        根据本地知识库：
        **《标题》 › 小节**
        正文…

        出处：标题 › 小节（path）
    """
    hits = [h for h in (hits or []) if str(h.get("text") or "").strip()]
    if not hits:
        return ""
    parts: list[str] = ["根据本地知识库："]
    used = 0
    sources: list[str] = []
    for h in hits:
        title = str(h.get("title") or "").strip() or "未命名"
        heading = str(h.get("heading") or "").strip()
        label = f"《{title}》" + (f" › {heading}" if heading else "")
        body = str(h.get("text") or "").strip()
        block = f"**{label}**\n{body}"
        if used + len(block) > max_chars:
            block = block[:max(0, max_chars - used)].rstrip() + "…"
        parts.append(block)
        used += len(block)
        sources.append(label + (f"（{h['path']}）" if h.get("path") else ""))
        if used >= max_chars:
            break
    if sources:
        parts.append("出处：" + "；".join(dict.fromkeys(sources)))
    return "\n\n".join(parts)


_DIRECT_RE = re.compile(
    r"^19:([0-9a-fA-F-]{36})_([0-9a-fA-F-]{36})@unq\.gbl\.spaces$")


def is_direct_id(conv_id: str) -> bool:
    """私聊判定：`19:<guid>_<guid>@unq.gbl.spaces`（群聊是 19:<opaque>@thread.v2）。

    注意**不能**用成员数：`memberProperties` 是单个成员对象（键固定为
    relationshipState/role），按它的键数判断会把所有会话都算成 2 人。
    """
    return bool(_DIRECT_RE.match(str(conv_id or "")))


def peer_guid(conv_id: str, skype_tenant: str = "") -> str:
    """私聊里"对方"的 GUID（两个 GUID 中排除 Skype 租户 id 的那个）。"""
    m = _DIRECT_RE.match(str(conv_id or ""))
    if not m:
        return ""
    a, b = m.group(1).lower(), m.group(2).lower()
    tenant = (skype_tenant or "").lower()
    if tenant and tenant in (a, b):
        return b if a == tenant else a
    return a


def inherit_authors(authors: list[str]) -> list[str]:
    """补齐连续消息的作者：Teams 只在"连续发言的第一条"显示名字，其余为空。

    实测（2026-11-02）：同一人连发 4 条，只有第 1 条有 message-author-name；
    按"空就继承上一条"补齐后，6 条里归属于他的正好是那 4 条（用户确认过）。
    开头的空值保持空（那时还不知道是谁）。
    """
    out: list[str] = []
    last = ""
    for a in authors or []:
        a = (a or "").strip()
        if a:
            last = a
        out.append(a or last)
    return out


def classify_chat(participants: int | None) -> str:
    """按参与人数判断会话类型：1:1（2 人）/ 群聊 / 未知。"""
    try:
        n = int(participants) if participants is not None else 0
    except (TypeError, ValueError):
        return "unknown"
    if n == 2:
        return "direct"
    if n > 2:
        return "group"
    return "unknown"


def should_answer(text: str, tokens: Iterable[str], seen: Iterable[str], key: str,
                  mine: bool) -> tuple[bool, str]:
    """一条消息要不要回：(要不要, 原因)。原因写日志，排障时不用猜。"""
    if mine:
        return False, "自己发的"
    if not has_mention(text, tokens):
        return False, "没有 @"
    q = extract_question(text, tokens)
    if not q:
        return False, "去掉 @ 后为空"
    if not is_new(key, seen):
        return False, "已回复过"
    return True, q
