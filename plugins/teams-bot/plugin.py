"""teams-bot 插件：Teams 网页版里"有人 @我"就回，**知识库优先**。

## 为什么这么做

正规 Teams 机器人要 Graph/Bot Framework 授权；这里要的是"同租户、本机、零授权"，
所以驱动已登录的 Teams 网页（`gantry/teams.py`）。判断逻辑全在 `gantry/teams_logic.py`
（可离线测），这个文件只做调度与 IO。

## 回答策略（三层，越往下越慢）

1. **知识库命中**（`kb.search` 得分 ≥ `kb_min_score`）→ 直接用资料拼答案，带出处，
   不调模型：快、可溯源、不会编；
2. **未命中但有 `fallback_agent`** → 调 Gantry 的 `run_agent`（Copilot 或已有提供商模型），
   提示词里**强制只依据资料**，资料不足要说"没有把握"；
3. 都没有 → （`reply_when_unknown`）回一句"知识库里没有，建议找 IT 服务台"。

## 安全默认

- `enabled: false` + `dry_run: true`：装上不会自己动，先在 `api/status` 里看它"**本来会回什么**"；
- 只回**别人发的**、含 `mention_tokens` 的、没见过的新消息（`seen` 去重，落盘）；
- 每个周期最多回 `max_replies_per_cycle` 条，避免刷屏。

## 配置（`data/plugins.json` 里 teams-bot.config 或插件面板）

见 `plugin.json` 的 config 段。关键几项：
- `chats`：只盯这些会话（标题片段，如 `["IT 支持群"]`）；空 = 扫描**未读**会话；
- `mention_tokens`：什么算"@我"（如 `["@Gantry", "@IT小助手"]`）；
- `my_name`：自己的显示名（用来排除"自己发的"，避免自问自答）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "dry_run": True,
    "interval_seconds": 30,
    "chats": [],
    "source": "idb",              # idb=从 IndexedDB 读会话（推荐）；dom=读当前打开的会话
    "send_enabled": False,        # 写侧还没打通（切会话），默认只记录不发送
    "only_direct": True,          # 只应答 1:1 会话（群聊默认跳过）
    "only_unread": True,          # 只处理未读会话（过滤历史消息，避免首次全量误回）
    "mention_tokens": ["@Gantry"],
    "my_name": "",
    "kb_first": True,
    "kb_min_score": 0.35,
    "kb_max_chars": 1200,
    "reply_when_unknown": True,
    "fallback_agent": True,
    "agent_mode": False,
    "model": "copilot-auto",
    "max_replies_per_cycle": 2,
    "seen_limit": 300,
}

UNKNOWN_TEXT = ("知识库里暂时没有这个问题，我先记下来。"
                "着急的话可以直接找 IT 服务台（分机 8000）。")


def _cfg(ctx, key: str) -> Any:
    """优先级：**运行时覆盖 > 插件配置 > 默认值**。

    覆盖来自 `POST api/settings`（写 state.overrides），这样不重启/不改文件也能开关；
    之前写成 config 优先，导致 settings 改了不生效（run 一直 skipped=disabled）。
    注意用 `in (None, "")` 判空而不是 `or`——`False` 是合法值（enabled/dry_run）。
    """
    ov = (ctx.state.get("overrides") or {})
    if key in ov and ov[key] not in (None, ""):
        return ov[key]
    v = (ctx.config or {}).get(key)
    return _DEFAULTS.get(key) if v in (None, "") else v


def _kb(ctx):
    from gantry.kb import KnowledgeBase

    kb = getattr(ctx, "_kb", None)
    if kb is None:
        kb = KnowledgeBase(_root(ctx))
        ctx._kb = kb
    return kb


def _root(ctx) -> Any:
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent


class Bot:
    def __init__(self, ctx):
        self.ctx = ctx
        self._task: asyncio.Task | None = None
        self._last_cycle: dict[str, Any] = {}
        self._last_error = ""
        self._last_kb = 0.0
        self._lock = asyncio.Lock()

    # ── 状态 ──
    def seen(self) -> list[str]:
        st = self.ctx.state
        val = st.get("seen")
        if not isinstance(val, list):
            val = []
            st["seen"] = val
        return val

    def stats(self) -> dict[str, Any]:
        st = self.ctx.state
        return {"replies": int(st.get("replies") or 0),
                "cycles": int(st.get("cycles") or 0),
                "last_cycle_at": st.get("last_cycle_at") or "",
                "last_error": self._last_error or st.get("last_error") or ""}

    def status(self) -> dict[str, Any]:
        cfg = {k: _cfg(self.ctx, k) for k in _DEFAULTS}
        return {
            "enabled": bool(cfg["enabled"]),
            "dry_run": bool(cfg["dry_run"]),
            "interval_seconds": float(cfg["interval_seconds"]),
            "chats": cfg["chats"],
            "only_direct": bool(cfg["only_direct"]),
            "only_unread": bool(cfg["only_unread"]),
            "source": cfg["source"],
            "send_enabled": bool(cfg["send_enabled"]),
            "mention_tokens": cfg["mention_tokens"],
            "my_name": cfg["my_name"],
            "model": cfg["model"],
            "kb": _kb(self.ctx).stats(),
            "seen": len(self.seen()),
            **self.stats(),
            "last_cycle": self._last_cycle,
        }

    # ── 回答 ──
    async def answer(self, question: str) -> tuple[str, str]:
        """返回 (回复文本, 来源说明) —— 来源给日志/接口看，不进回复正文。"""
        from gantry.teams_logic import build_kb_answer, pick_hits

        kb = _kb(self.ctx)
        try:
            hits = kb.search(question, 4)
        except Exception as exc:                 # noqa: BLE001
            hits = []
            self.ctx.log(f"知识库检索失败（继续走模型）: {exc}")
        good = pick_hits(hits, float(_cfg(self.ctx, "kb_min_score"))) \
            if bool(_cfg(self.ctx, "kb_first")) else []
        if good:
            text = build_kb_answer(question, good, int(_cfg(self.ctx, "kb_max_chars")))
            if text:
                return text, f"kb:{good[0].get('title')}"
        if bool(_cfg(self.ctx, "fallback_agent")):
            try:
                text = await self._agent_answer(question, good)
                if text:
                    return text, "model"
            except Exception as exc:             # noqa: BLE001
                self._last_error = f"模型回答失败: {exc}"
                self.ctx.log(f"模型回答失败: {exc}")
        if bool(_cfg(self.ctx, "reply_when_unknown")):
            return UNKNOWN_TEXT, "unknown"
        return "", "none"

    async def _agent_answer(self, question: str, hits: list[dict[str, Any]]) -> str:
        """让 Gantry 的模型回答。

        有知识库命中：严格只依据资料（不编造）；
        没有命中：用模型的通用 IT 知识回答——用户要的是"知识库没有就用 AI 回"，
        不能再强制模型说"资料里没有"。
        """
        from gantry import bridge as b

        context = "\n\n".join(
            f"《{h.get('title')}》" + (f" › {h['heading']}" if h.get("heading") else "")
            + "\n" + str(h.get("text") or "")
            for h in (hits or [])[:3])[:4000]
        if context:
            prompt = (
                "你是公司 IT 支持助手，在 Teams 里回答同事的提问。\n"
                "规则：\n"
                "1. 优先依据下面的《参考资料》回答；资料里没有的部分，用你的一般知识补充，"
                "但不要编造具体网址、分机号或内部流程。\n"
                "2. 回答用中文、口语化、分步骤，控制在 6 行以内。\n"
                "3. 不要提到「参考资料」这个词，也不要说自己是 AI。\n\n"
                f"《参考资料》\n{context}\n\n"
                f"同事的问题：{question}"
            )
        else:
            prompt = (
                "你是公司 IT 支持助手，在 Teams 里回答同事的提问。\n"
                "知识库里没有匹配的资料，请用你的一般 IT 知识直接回答，给出可操作的步骤；"
                "涉及公司内部系统/权限/具体分机号等你不确定的信息，提醒对方联系 IT 服务台核实，不要编造。\n"
                "回答用中文、口语化、分步骤，控制在 6 行以内；不要说自己是在查资料，也不要提 AI。\n\n"
                f"同事的问题：{question}"
            )
        req = b.AgentRequest(
            model=str(_cfg(self.ctx, "model") or "copilot-auto"),
            messages=[b.Message(role="user", content=prompt)],
            stream=False,
            session_id=f"teams-{int(time.time())}",
            workspace=None,
            agent_mode=bool(_cfg(self.ctx, "agent_mode")),
        )
        # `run_agent` 不读 agent_mode：它永远跑 Agent 循环，要求模型返回可解析的
        # JSON 动作。普通对话（agent_mode=false）必须走 plain_completions，
        # 否则模型正常的一段回答会被判成"未返回完整的 Agent JSON 动作" → 空回复。
        if bool(_cfg(self.ctx, "agent_mode")):
            result = await b.run_agent(req)
            return (getattr(result, "content", "") or "").strip()
        res = await b.plain_completions(req)
        try:
            return (res["choices"][0]["message"]["content"] or "").strip()
        except Exception:                        # noqa: BLE001
            return ""

    # ── 一个轮询周期 ──
    async def cycle(self) -> dict[str, Any]:
        from gantry import teams
        from gantry.teams_logic import (extract_question, has_mention, is_direct_id,
                                        mention_names, normalise_text, strip_mentions)

        async with self._lock:
            if not bool(_cfg(self.ctx, "enabled")):
                return {"skipped": "disabled"}
            tokens = list(_cfg(self.ctx, "mention_tokens") or [])
            my_name = str(_cfg(self.ctx, "my_name") or "")
            max_replies = int(_cfg(self.ctx, "max_replies_per_cycle") or 2)
            dry = bool(_cfg(self.ctx, "dry_run"))
            send_ok = (not dry) and bool(_cfg(self.ctx, "send_enabled"))
            seen = self.seen()
            out: dict[str, Any] = {"at": time.strftime("%H:%M:%S"), "source":
                                   str(_cfg(self.ctx, "source") or "idb"),
                                   "scanned": 0, "candidates": 0, "replied": 0,
                                   "handled": 0,
                                   "would": [], "skipped": [], "errors": []}
            # 知识库：最多每 60 秒增量重建一次
            if time.time() - self._last_kb > 60:
                try:
                    _kb(self.ctx).reindex()
                    self._last_kb = time.time()
                except Exception as exc:         # noqa: BLE001
                    self.ctx.log(f"知识库重建失败: {exc}")
            try:
                watch = await teams.api_idb_watch()
            except Exception as exc:             # noqa: BLE001
                self._last_error = str(exc)
                out["errors"].append(f"读会话失败: {str(exc)[:160]}")
                self._last_cycle = out
                return out
            if watch.get("error"):
                out["errors"].append(watch["error"])
                self._last_cycle = out
                return out
            # 优先用 list（top50，按最近活跃排序）；没有就退回 recent + directSample
            pool = watch.get("list") or []
            if not pool:
                pool = (watch.get("recent") or []) + (watch.get("directSample") or [])
            convs: list[dict[str, Any]] = []
            _seen_ids: set[str] = set()
            for c in pool:
                cid = str(c.get("id") or "")
                if not cid or cid in _seen_ids or not is_direct_id(cid):
                    continue
                _seen_ids.add(cid)
                convs.append(c)
            out["scanned"] = len(convs)
            for c in convs:
                text = normalise_text(str(c.get("lastText") or ""))
                who = str(c.get("lastFrom") or "")
                if not text:
                    continue
                # 只处理未读：历史消息（已读水位覆盖）直接跳过，避免首次启用
                # 把几十个旧会话的"最后一条"全回一遍。
                if bool(_cfg(self.ctx, "only_unread")) and not c.get("unread"):
                    out["skipped"].append({"id": c["id"][:24], "why": "已读"})
                    continue
                if my_name and who.strip().lower() == my_name.strip().lower():
                    out["skipped"].append({"id": c["id"][:24], "why": "自己发的"})
                    continue
                # Teams 的 @ 经常不在正文里（正文纯文本 + properties.mentions），
                # 只查 lastText 里的 "@" 会把真实 @ 全漏掉。
                mentions = mention_names(c.get("lastMentions"))
                mentioned = has_mention(text, tokens)
                if not mentioned and mentions:
                    if my_name:
                        mentioned = any(n.strip().lower() == my_name.strip().lower()
                                        for n in mentions)
                    else:
                        mentioned = True
                if not mentioned:
                    continue
                key = f"{c.get('id')}|{c.get('lastId') or c.get('lastAt')}"
                if key in seen:
                    continue
                q = strip_mentions(extract_question(text, tokens), mentions)
                if not q:
                    continue
                out["candidates"] += 1
                if out["handled"] >= max_replies:
                    continue
                try:
                    answer, source = await self.answer(q)
                except Exception as exc:         # noqa: BLE001
                    out["errors"].append(str(exc)[:160])
                    continue
                if not answer:
                    continue
                item = {"chat": (c.get("peerName") or c["id"][:24]), "question": q[:120],
                        "source": source, "sent": False, "text": answer[:400]}
                if send_ok:
                    try:
                        res = await teams.reply_to(
                            c.get("peerName") or c["id"], answer, send=True,
                            snippet=str(c.get("lastText") or "")[:40])
                        if res.get("sent"):
                            item["sent"] = True
                            out["replied"] += 1
                        else:
                            item["error"] = (res.get("error") or "发送未完成")[:160]
                    except Exception as exc:     # noqa: BLE001
                        item["error"] = str(exc)[:160]
                out["would"].append(item)
                out["handled"] += 1
                seen.append(key)
                del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
                self.ctx.save_state(seen=seen,
                                    replies=int(self.ctx.state.get("replies") or 0)
                                    + (1 if item["sent"] else 0))
                self.ctx.log(f"Teams[{source}] {item['chat']}: {q[:40]}")
            self._last_cycle = out
            self.ctx.state["cycles"] = int(self.ctx.state.get("cycles") or 0) + 1
            self.ctx.state["last_cycle_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            if out["errors"]:
                self._last_error = out["errors"][-1]
            return out

    async def loop(self) -> None:
        await asyncio.sleep(5)                    # 等宿主把插件都装好
        while True:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:              # noqa: BLE001
                self._last_error = str(exc)
                self.ctx.log(f"teams-bot 循环异常（继续）: {exc}")
            try:
                await asyncio.sleep(max(5.0, float(_cfg(self.ctx, "interval_seconds"))))
            except asyncio.CancelledError:
                raise


def _reload_backends() -> None:
    """热重载后端模块：改 `gantry/teams*.py` 后不必重启服务（重启要重新认证，很慢）。

    `teams_page`（已打开的 Teams 标签页）要**跨 reload 保留**，否则每次重载都会新开一个
    标签页、把原来那个留在后台。
    """
    import importlib

    from gantry import teams as _teams
    from gantry import teams_logic as _logic

    page = getattr(_teams, "teams_page", None)
    importlib.reload(_logic)
    importlib.reload(_teams)
    if page is not None:
        _teams.teams_page = page


def setup(ctx):
    try:
        _reload_backends()
    except Exception as exc:                     # noqa: BLE001
        ctx.log(f"热重载 teams 后端失败（继续用已加载的）: {exc}")
        try:
            from gantry import bridge as _b

            _b.log(f"teams-bot 热重载后端失败: {exc}")
        except Exception:                        # noqa: BLE001
            pass
    from gantry import teams
    from gantry.teams_logic import pick_hits

    bot = Bot(ctx)
    ctx._bot = bot

    # ── 工具（给 agent 用）──
    async def _teams_status() -> dict:
        return bot.status()

    async def _teams_reply(chat: str = "", text: str = "", force: bool = False) -> dict:
        if not (text or "").strip():
            return {"ok": False, "error": "text 不能为空"}
        if chat:
            if not await teams.open_chat(chat):
                return {"ok": False, "error": f"找不到会话: {chat}"}
        if bool(_cfg(ctx, "dry_run")) and not force:
            return {"ok": False, "error": "dry_run=true（加 force=true 才会真发）"}
        try:
            await teams.send_message(text)
            return {"ok": True, "chat": chat or "(当前会话)"}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    async def _teams_probe() -> dict:
        return await teams.probe(dump_dir=_root(ctx) / "data" / "teams-probe")

    async def _kb_search(query: str = "", limit: int = 4) -> dict:
        kb = _kb(ctx)
        try:
            kb.reindex()
        except Exception:                        # noqa: BLE001
            pass
        hits = kb.search(query, max(1, min(int(limit or 4), 10)))
        return {"query": query, "hits": hits,
                "filtered": pick_hits(hits, float(_cfg(ctx, "kb_min_score")))}

    async def _kb_reindex() -> dict:
        return _kb(ctx).reindex(force=False)

    ctx.register_tool("teams_status", _teams_status,
                      "Teams 机器人状态（是否启用、知识库规模、最近一轮结果）", {})
    ctx.register_tool("teams_reply", _teams_reply,
                      "在 Teams 指定会话里发一条消息（dry_run 下需 force=true）",
                      {"chat": "会话标题片段（空=当前会话）", "text": "内容",
                       "force": "绕过 dry_run"})
    ctx.register_tool("teams_probe", _teams_probe,
                      "校准用：dump Teams 页面命中的选择器/会话/消息，写 data/teams-probe/", {})
    ctx.register_tool("kb_search", _kb_search,
                      "查本地 IT 知识库（data/kb/*.md），返回带出处的片段",
                      {"query": "问题", "limit": "条数，默认 4"})
    ctx.register_tool("kb_reindex", _kb_reindex, "重建本地知识库索引（增量）", {})

    # ── HTTP 接口（WebUI 面板/排障用）──
    async def _api_status(request=None):
        return bot.status()

    async def _api_run(request=None):
        return await bot.cycle()

    async def _api_reply(request):
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        return await _teams_reply(chat=str(body.get("chat") or ""),
                                  text=str(body.get("text") or ""),
                                  force=bool(body.get("force")))

    async def _api_probe(request=None):
        return await _teams_probe()

    async def _api_reindex(request=None):
        return await _kb_reindex()

    async def _api_page_mimic(request=None):
        """页面内请求同源 chatsvc（cookie+令牌+客户端头，只读）。"""
        return await teams.api_page_mimic()

    async def _api_mimic(request=None):
        """用 authz 完整头 + 新令牌请求 chatsvcagg（只读）。"""
        return await teams.api_mimic_probe()

    async def _api_authz_types(request=None):
        """遍历 authz type 找可用于 chatsvc 的令牌（只读）。"""
        return await teams.api_authz_types()

    async def _api_replay_authz(request=None):
        """重放捕获的 authz 请求，拿新令牌并试 chatsvcagg（只读）。"""
        return await teams.api_replay_authz()

    async def _api_captured(request=None):
        """看当前页面钩子已捕获的请求/响应（只读）。"""
        return await teams.api_captured()

    async def _api_scroll_capture(request=None):
        """滚动消息区触发历史加载，看能否抓到 chatsvc 请求（只读）。"""
        return await teams.api_scroll_capture()

    async def _api_try_headers(request=None):
        """用已抓到的令牌试多种头风格（只读）。"""
        return await teams.api_try_headers()

    async def _api_tokens_all(request=None):
        """抓 authz 的所有令牌并逐个试 chatsvcagg（只读）。"""
        return await teams.api_tokens_all()

    async def _api_search_rows(request=None):
        """dump 搜索建议行（只读）。"""
        name = ""
        try:
            q = getattr(request, "query_params", {}) or {}
            name = str(q.get("name") or "")
        except Exception:                        # noqa: BLE001
            pass
        return await teams.api_search_rows(name)

    async def _api_compose(request):
        """写侧演练：打开会话并输入文本；?send=1 才真的点发送（默认不点）。"""
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        peer = str(body.get("peer") or "")
        text = str(body.get("text") or "")
        snippet = str(body.get("snippet") or "")
        send = bool(body.get("send"))
        if not peer or not text:
            return {"error": "需要 peer 和 text"}
        return await teams.reply_to(peer, text, send=send, snippet=snippet)

    async def _api_search_typing(request=None):
        """逐字敲搜索框打开会话（真实按键，触发 autosuggest）。"""
        name = ""
        try:
            q = getattr(request, "query_params", {}) or {}
            name = str(q.get("name") or "")
        except Exception:                        # noqa: BLE001
            pass
        if not name:
            return {"error": "需要 ?name=会话名"}
        return await teams.api_search_typing(name)

    async def _api_item_attrs(request=None):
        """dump 会话项属性（只读）。"""
        idx = 0
        try:
            q = getattr(request, "query_params", {}) or {}
            idx = int(q.get("idx") or 0)
        except Exception:                        # noqa: BLE001
            idx = 0
        return await teams.api_item_attrs(idx=idx)

    async def _api_click_test(request=None):
        """对会话项试多种点击方式（只读式，仅切换会话）。"""
        idx = 0
        try:
            q = getattr(request, "query_params", {}) or {}
            idx = int(q.get("idx") or 0)
        except Exception:                        # noqa: BLE001
            idx = 0
        return await teams.api_click_test(idx=idx)

    async def _api_open_url(request=None):
        """尝试用深链打开会话（只读，不发消息）。"""
        cid = ""
        try:
            q = getattr(request, "query_params", {}) or {}
            cid = str(q.get("id") or "")
        except Exception:                        # noqa: BLE001
            pass
        return await teams.api_open_by_url(cid)

    async def _api_search_probe(request=None):
        """搜索框结构（只读）。"""
        return await teams.api_search_probe()

    async def _api_open_search(request=None):
        """键盘搜索打开会话并校验（只读，不发消息）。"""
        name = ""
        try:
            q = getattr(request, "query_params", {}) or {}
            name = str(q.get("name") or "")
        except Exception:                        # noqa: BLE001
            pass
        if not name:
            return {"error": "需要 ?name=会话名"}
        confirm = 60.0
        try:
            q = getattr(request, "query_params", {}) or {}
            confirm = float(q.get("wait") or 60)
        except Exception:                        # noqa: BLE001
            pass
        res = await teams.api_open_by_search(name)      # 兼容旧签名
        # 自己再轮询一段时间：切换可能是"慢生效"的（实测出现过几秒后才切过去）
        if res.get("opened"):
            return res
        deadline = time.time() + max(10.0, confirm)
        while time.time() < deadline:
            await asyncio.sleep(3)
            try:
                head = await teams.header_title(teams.teams_page)
            except Exception:                    # noqa: BLE001
                head = ""
            if head and head != res.get("before"):
                res["after"] = head[:60]
                res["opened"] = True
                res["matched"] = name.lower()[:8] in head.lower()
                return res
        return res

    async def _api_idb_watch(request=None):
        """会话监看视图（IndexedDB，只读）：成员数 + 最后一条消息。"""
        return await teams.api_idb_watch()

    async def _api_idb_record(request=None):
        """看一条会话记录的原始结构（只读）。"""
        skip = 0
        try:
            q = getattr(request, "query_params", {}) or {}
            skip = int(q.get("skip") or 0)
        except Exception:                        # noqa: BLE001
            skip = 0
        return await teams.api_idb_record(skip=skip)

    async def _api_idb_convs(request=None):
        """读会话列表（IndexedDB，只读）。"""
        return await teams.api_idb_conversations()

    async def _api_idb_enum(request=None):
        """枚举关键 IndexedDB 库的仓库/行数/字段（只读）。"""
        return await teams.api_idb_enumerate()

    async def _api_idb(request=None):
        """读 Teams 的 IndexedDB 缓存结构（只读）。"""
        want = "conversation"
        try:
            q = getattr(request, "query_params", {}) or {}
            want = str(q.get("want") or "conversation")
        except Exception:                        # noqa: BLE001
            pass
        return await teams.api_idb_probe(want=want)

    async def _api_hosts_probe(request=None):
        """用当前令牌逐个候选 host 试（只读）。"""
        return await teams.api_hosts_probe()

    async def _api_decode_tokens(request=None):
        """解码令牌声明（aud/scp 等），判断 401 的原因。"""
        return await teams.api_decode_tokens()

    async def _api_typing_capture(request=None):
        """打字（不回车）触发一次 messaging 请求并抓头，随后清空输入框。"""
        return await teams.api_typing_capture()

    async def _api_sw_capture2(request=None):
        """用 CDP Target 转发抓 service worker 请求（只读）；?reload=0 不重载。"""
        rp = True
        try:
            q = getattr(request, "query_params", {}) or {}
            if str(q.get("reload", "")).lower() in ("0", "false", "no"):
                rp = False
        except Exception:                        # noqa: BLE001
            pass
        return await teams.api_sw_capture2(reload_page=rp)

    async def _api_sw_capture(request=None):
        """挂 CDP 到 service worker，抓 App 自己发往 chatsvc 的请求头（只读）。"""
        return await teams.api_sw_capture()

    async def _api_cookie_probe(request=None):
        """同源 chatsvc 的几种鉴权方式对比（只读）。"""
        return await teams.api_cookie_probe()

    async def _api_gtm(request=None):
        """列出区域配置里的 host（找 chatsvc 真实地址用，只读）。"""
        return await teams.api_gtm_hosts()

    async def _api_hook_capture(request=None):
        """装钩子并重载 Teams 页，抓真实 Authorization（只读，不发消息）。

        `?reload=0` 复用当前页（不刷新），用于二次查看已抓到的条目。
        """
        reload_page = True
        try:
            q = getattr(request, "query_params", {}) or {}
            if str(q.get("reload", "")).lower() in ("0", "false", "no"):
                reload_page = False
        except Exception:                        # noqa: BLE001
            pass
        return await teams.api_hook_capture(reload=reload_page)

    async def _api_probe_api(request=None):
        """诊断网页客户端内部接口（只读）：令牌/会话/消息。"""
        return await teams.api_probe()

    async def _api_settings(request):
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        overrides = dict(ctx.state.get("overrides") or {})
        for k in ("enabled", "dry_run", "send_enabled", "source", "chats",
                  "mention_tokens", "my_name", "model", "interval_seconds",
                  "kb_min_score", "fallback_agent", "agent_mode", "only_direct",
                  "only_unread"):
            if k in body:
                overrides[k] = body[k]
        ctx.save_state(overrides=overrides)
        return {"ok": True, "overrides": overrides, "status": bot.status()}

    ctx.register_api("GET", "api/status", _api_status)
    ctx.register_api("POST", "api/run", _api_run)
    ctx.register_api("POST", "api/reply", _api_reply)
    ctx.register_api("GET", "api/probe", _api_probe)
    ctx.register_api("POST", "api/reindex", _api_reindex)
    ctx.register_api("GET", "api/probe-api", _api_probe_api)
    ctx.register_api("GET", "api/hook-capture", _api_hook_capture)
    ctx.register_api("GET", "api/gtm", _api_gtm)
    ctx.register_api("GET", "api/cookie-probe", _api_cookie_probe)
    ctx.register_api("GET", "api/sw-capture", _api_sw_capture)
    ctx.register_api("GET", "api/sw-capture2", _api_sw_capture2)
    ctx.register_api("GET", "api/typing-capture", _api_typing_capture)
    ctx.register_api("GET", "api/decode-tokens", _api_decode_tokens)
    ctx.register_api("GET", "api/hosts-probe", _api_hosts_probe)
    ctx.register_api("GET", "api/idb", _api_idb)
    ctx.register_api("GET", "api/idb-enum", _api_idb_enum)
    ctx.register_api("GET", "api/idb-convs", _api_idb_convs)
    ctx.register_api("GET", "api/idb-record", _api_idb_record)
    ctx.register_api("GET", "api/idb-watch", _api_idb_watch)
    ctx.register_api("GET", "api/open-search", _api_open_search)
    ctx.register_api("GET", "api/search-probe", _api_search_probe)
    ctx.register_api("GET", "api/open-url", _api_open_url)
    ctx.register_api("GET", "api/item-attrs", _api_item_attrs)
    ctx.register_api("GET", "api/search-typing", _api_search_typing)
    ctx.register_api("POST", "api/compose", _api_compose)
    ctx.register_api("GET", "api/search-rows", _api_search_rows)
    ctx.register_api("GET", "api/click-test", _api_click_test)
    ctx.register_api("GET", "api/tokens-all", _api_tokens_all)
    ctx.register_api("GET", "api/try-headers", _api_try_headers)
    ctx.register_api("GET", "api/captured", _api_captured)
    ctx.register_api("GET", "api/replay-authz", _api_replay_authz)
    ctx.register_api("GET", "api/authz-types", _api_authz_types)
    ctx.register_api("GET", "api/mimic", _api_mimic)
    ctx.register_api("GET", "api/page-mimic", _api_page_mimic)
    ctx.register_api("GET", "api/scroll-capture", _api_scroll_capture)
    ctx.register_api("POST", "api/settings", _api_settings)

    ctx.register_panel({"id": "status", "title": "Teams 机器人", "view": "table",
                        "api": {"method": "GET", "path": "api/status"},
                        "hint": "知识库优先；默认 dry_run，不会真发消息"})

    try:
        bot._task = asyncio.ensure_future(bot.loop())
    except RuntimeError:
        bot._task = None
        ctx.log("teams-bot: 无事件循环，轮询未启动（工具/接口仍可用）")
    ctx.log("teams-bot 已就绪（默认 enabled=false / dry_run=true）")


def teardown(ctx):
    task = getattr(getattr(ctx, "_bot", None), "_task", None)
    if task is not None:
        try:
            task.cancel()
        except Exception:                        # noqa: BLE001
            pass
    bot = getattr(ctx, "_bot", None)
    if bot is not None and getattr(ctx, "_bot", None) is bot:
        pass
    kb = getattr(ctx, "_kb", None)
    if kb is not None:
        try:
            kb.close()
        except Exception:                        # noqa: BLE001
            pass
