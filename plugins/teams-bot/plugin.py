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
import hashlib
import time
from typing import Any

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "dry_run": True,
    "interval_seconds": 30,
    "chats": [],
    "source": "idb",              # idb=聊天列表；mentions=Chat→Mentions 全局 @我 列表
    "send_enabled": False,        # 写侧还没打通（切会话），默认只记录不发送
    "only_direct": True,          # 只应答 1:1 会话（群聊默认跳过）
    "only_unread": True,          # 只处理未读会话（过滤历史消息，避免首次全量误回）
    "mention_tokens": ["@Gantry"],
    "my_name": "",
    "my_user_id": "",             # 本账号 Entra Object ID；1:1 会话里区分我和对方
    "typing_delay_ms": 8,         # 逐字输入延迟；太大容易撞发送超时
    "send_jitter_ms": 0,          # 发送前随机等待上限（拟人化）
    "mentions_gate": True,        # 先看 Mentions 未读 badge，0 就不展开列表
    "kb_first": True,
    "kb_min_score": 0.5,
    # 命中还必须覆盖至少这么多查询词（纯向量相似但词面无关的不算）
    "kb_min_coverage": 0.34,
    "kb_max_chars": 1200,
    "kb_synthesize": True,        # 知识库命中后让模型组织成"回答"，而不是整段抄资料
    "reply_when_unknown": True,
    "fallback_agent": True,
    "agent_mode": False,
    "model": "copilot-auto",
    "max_replies_per_cycle": 2,
    "seen_limit": 300,
    "send_timeout_seconds": 45,
    # Teams 未登录时不要每轮都撞：退避这么久再试
    "auth_backoff_seconds": 300,
    "outbox_path": "data/teams-bot-sends.db",
    "outbox_lease_seconds": 300,
    # ── 提示词 / 兜底文本（可在插件面板或 api/settings 自定义，改完即时生效）──
    # 有知识库命中时的系统提示词（{context} = 资料、{question} = 同事的问题）
    "prompt_with_kb": (
        "你是公司 IT 支持助手，在 Teams 里回答同事的提问。\n"
        "规则：\n"
        "1. 优先依据下面的《参考资料》回答；资料里没有的部分，用你的一般知识补充，"
        "但不要编造具体网址、分机号或内部流程。\n"
        "2. 不要整段复制/粘贴参考资料，要用自己的话针对问题给出可操作步骤；"
        "资料与问题无关时不要硬套，直接按一般 IT 知识回答并说明需要核实。\n"
        "3. 回答用中文、口语化、分步骤，控制在 6 行以内。\n"
        "4. 不要提到「参考资料」这个词，也不要说自己是 AI。\n"
        "5. 不要追问、不要要求用户补截图/补信息，也不要用「如果需要…」「把…发我」"
        "这类收尾；最后一条就是可执行步骤。\n\n"
        "《参考资料》\n{context}\n\n"
        "同事的问题：{question}"
    ),
    # 知识库没命中时的系统提示词（{question} = 同事的问题）
    "prompt_no_kb": (
        "你是公司 IT 支持助手，在 Teams 里回答同事的提问。\n"
        "知识库里没有匹配的资料，请用你的一般 IT 知识直接回答，给出可操作的步骤；"
        "涉及公司内部系统/权限/具体分机号等你不确定的信息，提醒对方联系 IT 服务台核实，不要编造。\n"
        "回答用中文、口语化、分步骤，控制在 6 行以内；不要说自己是在查资料，也不要提 AI。\n\n"
        "同事的问题：{question}"
    ),
    # 模型也答不上来（或超时）时的兜底回复；留空则不回复
    "unknown_text": ("知识库里暂时没有这个问题，我先记下来。"
                    "着急的话可以直接找 IT 服务台（分机 8000）。"),
}


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


def _sem(ctx):
    """按 config.json 的 kb.embedding 构造/缓存向量索引；不可用时返回 None。"""
    from gantry.kb_semantic import index_for

    if getattr(ctx, "_sem", "missing") == "missing":
        try:
            from gantry.bridge import CFG as _CFG

            cfg = ((_CFG.get("kb") or {}).get("embedding") or {})
            ctx._sem = index_for(_kb(ctx), dict(cfg))
        except Exception as exc:                 # noqa: BLE001
            ctx.log(f"向量层初始化失败（退回 FTS）: {exc}")
            ctx._sem = None
    return getattr(ctx, "_sem", None)


def _search(ctx, question: str, limit: int = 4) -> list:
    """知识库检索：有向量索引就混合（0.75×余弦 + 0.25×覆盖率），否则纯 FTS。"""
    sem = _sem(ctx)
    if sem is not None:
        try:
            st = sem.stats()
            if st["vectorised"] < st["chunks"]:
                sem.build()
            hits = sem.hybrid(question, limit)
            if hits:
                return hits
        except Exception as exc:                 # noqa: BLE001
            ctx.log(f"混合检索失败（退回 FTS）: {exc}")
    return _kb(ctx).search(question, limit)


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
        # P0：鉴权失败退避（epoch 秒）与最近会话状态
        self._auth_until = 0.0
        self._auth_seen = ""
        # P1：发送队列（cycle 只入队，worker 串行发）
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None
        self._sending = False
        self._sent_count = 0
        self._cycle_started = 0.0

    # ── 发送幂等箱（持久化，重启后仍能防重复）──
    def outbox(self):
        ob = getattr(self.ctx, "_teams_outbox", None)
        if ob is None:
            from pathlib import Path

            from gantry.teams_outbox import SendOutbox

            path = str(_cfg(self.ctx, "outbox_path") or "data/teams-bot-sends.db")
            if path != ":memory:" and not Path(path).is_absolute():
                path = str(_root(self.ctx) / path)
            ob = SendOutbox(path, lease_seconds=float(
                _cfg(self.ctx, "outbox_lease_seconds") or 300))
            self.ctx._teams_outbox = ob
        return ob

    def _model_ready(self) -> bool:
        """Copilot 网页被区域限制/未登录时，别每轮都等模型超时。"""
        model = str(_cfg(self.ctx, "model") or "copilot-auto")
        if not model.startswith("copilot"):
            return True
        try:
            from gantry.api import BOOT

            if BOOT.get("browser_state") in ("idle", "starting"):
                return True                 # 浏览器还没起：留给懒启动判断
            if not BOOT.get("copilot_ready"):
                return False
        except Exception:                    # noqa: BLE001
            return True
        try:
            from gantry import bridge as b

            return "blocked" not in b.page_url_safe()
        except Exception:                    # noqa: BLE001
            return True

    # ── 状态 ──
    def seen(self) -> list[str]:
        st = self.ctx.state
        val = st.get("seen")
        if not isinstance(val, list):
            val = []
            st["seen"] = val
        return val

    # ── P1：发送队列 ──
    def _ensure_worker(self) -> None:
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_event_loop().create_task(self._send_loop())

    async def _send_loop(self) -> None:
        assert self._queue is not None
        while True:
            job = await self._queue.get()
            self._sending = True
            try:
                await self._do_send(job)
            except Exception as exc:             # noqa: BLE001
                self._last_error = f"发送异常: {str(exc)[:160]}"
                self.ctx.log(f"发送异常: {str(exc)[:200]}")
            finally:
                self._sending = False
                self._queue.task_done()

    async def _do_send(self, job: dict[str, Any]) -> None:
        from gantry import teams

        item = job["item"]
        timeout = max(10.0, float(job.get("timeout") or 45))
        try:
            res = await asyncio.wait_for(
                teams.reply_to(job["peer"], job["answer"], send=True,
                               snippet=job["snippet"],
                               conv_id=job.get("conv_id", job["cid"]),
                               expected=job["expected"], strict=True,
                               typing_delay_ms=int(_cfg(self.ctx, "typing_delay_ms") or 8),
                               send_jitter_ms=int(_cfg(self.ctx, "send_jitter_ms") or 0)),
                timeout=timeout)
        except asyncio.TimeoutError:
            item["error"] = f"发送超时（>{timeout:.0f}s）"
            self.outbox().mark_failed(job["cid"], job["mid"], item["error"], unknown=True)
            return
        except Exception as exc:                 # noqa: BLE001
            item["error"] = str(exc)[:160]
            self.outbox().mark_failed(job["cid"], job["mid"], item["error"], unknown=True)
            return
        if res.get("sent"):
            item["sent"] = True
            self.outbox().mark_sent(job["cid"], job["mid"])
            self._sent_count += 1
            self.ctx.save_state(replies=int(self.ctx.state.get("replies") or 0) + 1)
            self.ctx.log(f"Teams 已发送 → {job['peer']}: {job['answer'][:40]}")
        else:
            item["error"] = (res.get("error") or "发送未完成")[:160]
            self.outbox().mark_failed(job["cid"], job["mid"], item["error"],
                                      unknown=(res.get("reason") != "identity_mismatch"))

    async def drain(self) -> None:
        """测试用：等队列清空后停掉 worker。"""
        if self._queue is not None:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=30)
            except asyncio.TimeoutError:
                pass
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except BaseException:                # noqa: BLE001
                pass
            self._worker = None

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
            "busy": bool(self._lock.locked()),
            "session": {
                "auth": self._auth_seen or "unknown",
                "retry_at": time.strftime("%H:%M:%S", time.localtime(self._auth_until))
                if self._auth_until > time.time() else "",
            },
            "queue_size": self._queue.qsize() if self._queue is not None else 0,
            "send_timeout_seconds": float(cfg.get("send_timeout_seconds") or 45),
            "answer_timeout_seconds": float(cfg.get("answer_timeout_seconds") or 60),
            "auth_backoff_seconds": float(cfg.get("auth_backoff_seconds") or 300),
            "sending": self._sending,
            "outbox": self.outbox().stats(),
            **self.stats(),
            "last_cycle": self._last_cycle,
        }

    # ── 回答 ──
    async def answer(self, question: str) -> tuple[str, str]:
        """返回 (回复文本, 来源说明) —— 来源给日志/接口看，不进回复正文。"""
        timeout = float(_cfg(self.ctx, "answer_timeout_seconds") or 60)
        try:
            return await asyncio.wait_for(self._answer_inner(question), timeout=timeout)
        except asyncio.TimeoutError:
            self._last_error = f"回答超时（>{timeout:.0f}s）"
            self.ctx.log(f"回答超时（{timeout:.0f}s）：{question[:40]}")
            if bool(_cfg(self.ctx, "reply_when_unknown")):
                return self._unknown_text(), "timeout"
            return "", "timeout"

    async def _answer_inner(self, question: str) -> tuple[str, str]:
        from gantry.teams_logic import build_kb_answer, pick_hits

        try:
            hits = _search(self.ctx, question, 4)
        except Exception as exc:                 # noqa: BLE001
            hits = []
            self.ctx.log(f"知识库检索失败（继续走模型）: {exc}")
        good = pick_hits(hits, float(_cfg(self.ctx, "kb_min_score")),
                         query=question,
                         min_coverage=float(_cfg(self.ctx, "kb_min_coverage") or 0)) \
            if bool(_cfg(self.ctx, "kb_first")) else []
        if good:
            # 命中文档 ≠ 可读回答：整段抄资料会让人感觉"没有智能"。
            # 优先让模型依据资料组织成针对问题的步骤；模型挂了再退回原文摘录。
            if bool(_cfg(self.ctx, "kb_synthesize")) and \
                    bool(_cfg(self.ctx, "fallback_agent")) and self._model_ready():
                try:
                    text = await self._agent_answer(question, good)
                    if text:
                        label = "；".join(dict.fromkeys(
                            f"《{h.get('title')}》" +
                            (f" › {h['heading']}" if h.get("heading") else "")
                            for h in good[:2]))
                        return f"{text}\n\n来源：{label}", f"model+kb:{good[0].get('title')}"
                except Exception as exc:         # noqa: BLE001
                    self._last_error = f"知识库回答生成失败（退回摘录）: {exc}"
                    self.ctx.log(f"知识库回答生成失败（退回摘录）: {exc}")
            text = build_kb_answer(question, good, int(_cfg(self.ctx, "kb_max_chars")))
            if text:
                return text, f"kb:{good[0].get('title')}"
        if bool(_cfg(self.ctx, "fallback_agent")) and self._model_ready():
            try:
                text = await self._agent_answer(question, good)
                if text:
                    return text, "model"
            except Exception as exc:             # noqa: BLE001
                self._last_error = f"模型回答失败: {exc}"
                self.ctx.log(f"模型回答失败: {exc}")
        if bool(_cfg(self.ctx, "reply_when_unknown")):
            return self._unknown_text(), "unknown"
        return "", "none"

    def _unknown_text(self) -> str:
        return str(_cfg(self.ctx, "unknown_text") or "").strip()

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
            prompt = str(_cfg(self.ctx, "prompt_with_kb") or _DEFAULTS["prompt_with_kb"]).format(
                context=context, question=question)
        else:
            prompt = str(_cfg(self.ctx, "prompt_no_kb") or _DEFAULTS["prompt_no_kb"]).format(
                context=context, question=question)
        model = str(_cfg(self.ctx, "model") or "copilot-auto")
        sid = f"teams-{int(time.time())}"
        req = b.AgentRequest(
            model=model,
            messages=[b.Message(role="user", content=prompt)],
            stream=False,
            session_id=sid,
            workspace=None,
            agent_mode=bool(_cfg(self.ctx, "agent_mode")),
        )
        if bool(_cfg(self.ctx, "agent_mode")):
            result = await b.run_agent(req)
            content = (getattr(result, "content", "") or "").strip()
        else:
            res = await b.plain_completions(req)
            try:
                content = (res["choices"][0]["message"]["content"] or "").strip()
            except Exception:                    # noqa: BLE001
                content = ""
        # Copilot 侧栏的"后续建议"按钮文字会被 inner_text 一起读进来，去掉
        content = b.strip_suggestion_tail(content)
        # 会话要留在侧栏里才能被「⋯ → 删除会话」回收（连带删远端 Copilot 会话）；
        # 标题改成可读的，别让一屏都是提示词第一行。
        _q = " ".join(str(question or "").split())[:40]
        try:
            b.AGENT_SESSIONS.patch(sid, title=f"Teams: {_q}", tag="Teams")
        except Exception:                        # noqa: BLE001
            pass
        return content

    # ── 一个轮询周期 ──
    async def cycle(self) -> dict[str, Any]:
        from gantry import teams
        from gantry.teams_logic import (extract_question, has_mention, is_direct_id,
                                        mention_names, normalise_text, peer_guid,
                                        strip_leading_name, strip_mentions)

        if self._lock.locked():
            return {"at": time.strftime("%H:%M:%S"), "skipped": "busy",
                    "error": "上一轮轮询/手动运行还没结束"}
        async with self._lock:
            self._cycle_started = time.time()
            if not bool(_cfg(self.ctx, "enabled")):
                return {"skipped": "disabled"}
            # P0：上次鉴权失败且还在退避期内 —— 直接返回，不去碰浏览器
            if time.time() < self._auth_until:
                return {"at": time.strftime("%H:%M:%S"), "skipped": "auth_backoff",
                        "auth": self._auth_seen,
                        "retry_in": round(self._auth_until - time.time())}
            tokens = list(_cfg(self.ctx, "mention_tokens") or [])
            my_name = str(_cfg(self.ctx, "my_name") or "")
            max_replies = int(_cfg(self.ctx, "max_replies_per_cycle") or 2)
            dry = bool(_cfg(self.ctx, "dry_run"))
            send_ok = (not dry) and bool(_cfg(self.ctx, "send_enabled"))
            send_timeout = max(10.0, float(_cfg(self.ctx, "send_timeout_seconds") or 120))
            seen = self.seen()
            last_ids = dict(self.ctx.state.get("last_ids") or {})
            out: dict[str, Any] = {"at": time.strftime("%H:%M:%S"), "source":
                                   str(_cfg(self.ctx, "source") or "idb"),
                                   "scanned": 0, "candidates": 0, "replied": 0,
                                   "queued": 0, "handled": 0,
                                   "would": [], "skipped": [], "errors": []}
            # 知识库：最多每 60 秒增量重建一次
            if time.time() - self._last_kb > 60:
                try:
                    _kb(self.ctx).reindex()
                    self._last_kb = time.time()
                except Exception as exc:         # noqa: BLE001
                    self.ctx.log(f"知识库重建失败: {exc}")
            # 读侧可选：idb（默认，扫聊天列表）或 mentions（全局 @我 列表）
            src = str(_cfg(self.ctx, "source") or "idb").strip().lower()
            if src == "mentions":
                return await self._cycle_mentions(
                    out, my_name=my_name, max_replies=max_replies,
                    dry=dry, send_ok=send_ok, send_timeout=send_timeout,
                    seen=seen)
            # P2：只取上次水位之后有变化的会话（首轮 since=0 全量）
            since = float(self.ctx.state.get("idb_watermark") or 0)
            try:
                watch = await teams.api_idb_watch(
                    since=since, my_guid=str(_cfg(self.ctx, "my_user_id") or ""))
            except teams.TeamsAuthRequired as exc:
                backoff = float(_cfg(self.ctx, "auth_backoff_seconds") or 300)
                self._auth_until = time.time() + backoff
                self._auth_seen = "expired"
                self._last_error = str(exc)
                out["errors"].append(
                    f"Teams 未登录（{backoff:.0f}s 后重试）: {str(exc)[:120]}")
                self._last_cycle = out
                self.ctx.log(f"Teams 需要登录，退避 {backoff:.0f}s：{str(exc)[:80]}")
                return out
            except Exception as exc:
                self._last_error = str(exc)
                self._auth_seen = "error"
                out["errors"].append(f"读会话失败: {str(exc)[:160]}")
                self._last_cycle = out
                return out
            self._auth_seen = "ok"
            self._auth_until = 0.0
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
            # 白名单：`chats` 非空时只处理标题/对端名匹配的会话（否则会扫全部未读）
            allow = [str(x).strip().lower() for x in (_cfg(self.ctx, "chats") or []) if str(x).strip()]
            if allow:
                def _hit(c: dict[str, Any]) -> bool:
                    hay = f"{c.get('peerName') or ''} {c.get('topic') or ''}".lower()
                    return any(a in hay for a in allow)
                before = len(convs)
                convs = [c for c in convs if _hit(c)]
                out["filtered"] = before - len(convs)
            out["scanned"] = len(convs)
            for c in convs:
                cid = str(c.get("id") or "")
                lid = str(c.get("lastId") or c.get("lastAt") or "")
                prev = last_ids.get(cid)
                # 最后一条消息 id 变了 = 有新消息（即使会话开着、被自动标成已读）
                new_msg = bool(lid) and prev is not None and lid != prev
                if lid:
                    last_ids[cid] = lid
                text = normalise_text(str(c.get("lastText") or ""))
                who = str(c.get("lastFrom") or "")
                # Teams 会话最后一条可能是 ThreadActivity/AddMember 等非聊天消息；
                # 这类消息的正文是内部 id/系统事件，不能拿来回答（会生成无关回复）。
                mtype = str(c.get("lastType") or "").strip().lower()
                if mtype and mtype not in ("chat", "text", "richtext", "richtext/html"):
                    out["skipped"].append({"id": cid[:24], "why": f"非聊天消息:{mtype}"})
                    continue
                if not text:
                    continue
                # 只处理未读：历史消息（已读水位覆盖）直接跳过，避免首次启用
                # 把几十个旧会话的"最后一条"全回一遍。
                # 例外：lastId 变了的新消息——即使会话开着被自动标已读，也要回。
                if bool(_cfg(self.ctx, "only_unread")) and not c.get("unread") and not new_msg:
                    out["skipped"].append({"id": cid[:24], "why": "已读"})
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
                if my_name:
                    q = strip_leading_name(q, my_name) or q
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
                # 幂等键：会话 + 触发回复的那条消息。Teams 的 lastId 就是消息 id；
                # 没有 lastId 时用时间+文本哈希兜底（重启后仍稳定）。
                mid = str(c.get("lastId") or "").strip()
                if not mid:
                    mid = "fallback:" + hashlib.sha1(
                        f"{c.get('lastAt')}|{c.get('lastText')}".encode("utf-8")
                    ).hexdigest()[:16]
                if send_ok:
                    reservation = self.outbox().reserve(
                        cid, mid, text=q, reply=answer)
                    item["idempotency"] = reservation.get("action")
                    if not reservation.get("send"):
                        item["error"] = str(reservation.get("error") or
                                            "幂等箱拒绝发送")[:160]
                        out["skipped"].append({"id": cid[:24],
                                               "why": reservation.get("action")})
                    else:
                        # P1：入队，由独立 worker 串行发送；cycle 不再被发送阻塞
                        self._ensure_worker()
                        # 搜索片段同样不能带 "@自己名字" 前缀，否则 Teams 搜索定位会失败
                        _snip_raw = str(c.get("lastText") or "")
                        _snip = strip_leading_name(_snip_raw, my_name) or _snip_raw
                        self._queue.put_nowait({
                            "cid": cid,
                            "mid": mid,
                            "answer": answer,
                            "snippet": _snip[:40],
                            "peer": c.get("peerName") or c["id"],
                            "expected": {
                                "conv_id": cid,
                                "peer": c.get("peerName") or "",
                                "peer_guid": c.get("peerGuid") or peer_guid(
                                    cid, str(watch.get("skypeTenant") or "")),
                                "topic": c.get("topic") or "",
                                "snippet": _snip,
                                "message_id": mid,
                            },
                            "timeout": send_timeout,
                            "item": item,
                        })
                        item["queued"] = True
                        out["queued"] += 1
                out["would"].append(item)
                out["handled"] += 1
                seen.append(key)
                del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
                self.ctx.save_state(seen=seen,
                                    replies=int(self.ctx.state.get("replies") or 0)
                                    + (1 if item["sent"] else 0))
                self.ctx.log(f"Teams[{source}] {item['chat']}: {q[:40]}")
            self._last_cycle = out
            # P2：记录本轮最大 lastTime 作为水位（只增不减）
            newest = max([float(c.get("lastTime") or 0) for c in (pool or [])] or [0])
            if newest > since:
                self.ctx.state["idb_watermark"] = newest
            cycles = int(self.ctx.state.get("cycles") or 0) + 1
            at = time.strftime("%Y-%m-%d %H:%M:%S")
            self.ctx.state["cycles"] = cycles
            self.ctx.state["last_cycle_at"] = at
            self.ctx.save_state(last_ids=last_ids, cycles=cycles, last_cycle_at=at)
            if out["errors"]:
                self._last_error = out["errors"][-1]
            return out

    async def _cycle_mentions(self, out, *, my_name, max_replies, dry,
                              send_ok, send_timeout, seen):
        """读 Chat → Mentions，点行打开会话后回答（source=mentions）。"""
        from gantry import teams
        from gantry.teams_common import _norm_identity

        try:
            data = await teams.read_mentions(
                my_name=my_name, gate=bool(_cfg(self.ctx, "mentions_gate")))
        except teams.TeamsAuthRequired as exc:
            backoff = float(_cfg(self.ctx, "auth_backoff_seconds") or 300)
            self._auth_until = time.time() + backoff
            self._auth_seen = "expired"
            self._last_error = str(exc)
            out["errors"].append(
                f"Teams 未登录（{backoff:.0f}s 后重试）: {str(exc)[:120]}")
            self._last_cycle = out
            self.ctx.log(f"Teams 需要登录，退避 {backoff:.0f}s：{str(exc)[:80]}")
            return out
        except Exception as exc:                 # noqa: BLE001
            self._last_error = str(exc)
            self._auth_seen = "error"
            out["errors"].append(f"读 Mentions 失败: {str(exc)[:160]}")
            self._last_cycle = out
            return out
        self._auth_seen = "ok"
        self._auth_until = 0.0

        rows = data.get("rows") or []
        out["scanned"] = len(rows)
        out["badge"] = data.get("badge")
        seen_set = set(seen)
        handled = 0
        only_direct = bool(_cfg(self.ctx, "only_direct"))
        allow = [str(x).strip().lower()
                 for x in (_cfg(self.ctx, "chats") or []) if str(x).strip()]
        for row in rows:
            q = str(row.get("question") or row.get("message") or "").strip()
            if not q:
                continue
            sender = str(row.get("sender") or "").strip()
            if my_name and _norm_identity(sender) == _norm_identity(my_name):
                out["skipped"].append({"id": str(row.get("key") or "")[:24],
                                       "why": "自己发的"})
                continue
            key = f"mention|{row.get('key') or q}"
            if key in seen_set:
                continue
            out["candidates"] += 1
            if handled >= max_replies:
                continue
            try:
                opened = await teams.open_mention(row, my_name=my_name)
            except Exception as exc:             # noqa: BLE001
                out["errors"].append(f"打开提及失败: {str(exc)[:160]}")
                continue
            if not opened.get("opened"):
                out["errors"].append(opened.get("error") or f"打不开会话: {sender}")
                continue
            chat = str(opened.get("chat") or sender or "").strip()
            # Mentions 也可能来自群聊：白名单同时匹配发送人和聊天标题。
            sender_ok = (not allow) or any(a in sender.lower() for a in allow)
            chat_ok = (not allow) or any(a in chat.lower() for a in allow)
            if allow and not (sender_ok or chat_ok):
                out["skipped"].append({"id": key[:24], "why": "不在 chats 白名单"})
                seen.append(key)
                seen_set.add(key)
                del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
                self.ctx.save_state(seen=seen)
                continue
            if only_direct:
                try:
                    kind = await teams.chat_kind()
                except Exception:                # noqa: BLE001
                    kind = "unknown"
                if kind == "group":
                    out["skipped"].append({"id": key[:24], "why": "群聊（only_direct）"})
                    seen.append(key)
                    seen_set.add(key)
                    del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
                    self.ctx.save_state(seen=seen)
                    continue
            try:
                answer, source = await self.answer(q)
            except Exception as exc:             # noqa: BLE001
                out["errors"].append(str(exc)[:160])
                continue
            if not answer:
                continue
            item = {"chat": chat or sender, "question": q[:120],
                    "source": source, "sent": False, "text": answer[:400]}
            if dry or not send_ok:
                item["dry_run"] = True
                out["would"].append(item)
                seen.append(key)
                seen_set.add(key)
                del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
                self.ctx.save_state(seen=seen)
                handled += 1
                continue
            mid = str(row.get("time") or row.get("key") or "")[:60]
            try:
                reserved = self.outbox().reserve(key, mid, text=answer)
            except Exception as exc:             # noqa: BLE001
                out["errors"].append(f"outbox 预留失败: {str(exc)[:160]}")
                continue
            if not reserved:
                continue
            self._ensure_worker()
            self._queue.put_nowait({
                "cid": key, "conv_id": "", "mid": mid,
                "answer": answer, "snippet": q[:40],
                "peer": chat or sender,
                "expected": {"peer": chat or sender, "snippet": q},
                "timeout": send_timeout, "item": item,
            })
            item["queued"] = True
            out["queued"] += 1
            out["would"].append(item)
            handled += 1
            seen.append(key)
            seen_set.add(key)
            del seen[:-int(_cfg(self.ctx, "seen_limit") or 300)]
            self.ctx.save_state(seen=seen)
            self.ctx.log(f"Teams[mentions] {item['chat']}: {q[:40]}")
        out["handled"] = handled
        self._last_cycle = out
        cycles = int(self.ctx.state.get("cycles") or 0) + 1
        at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.ctx.state["cycles"] = cycles
        self.ctx.state["last_cycle_at"] = at
        self.ctx.save_state(cycles=cycles, last_cycle_at=at)
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
    from gantry import teams_api as _api
    from gantry import teams_common as _common
    from gantry import teams_dom as _dom
    from gantry import teams_logic as _logic
    from gantry import teams_mentions as _mentions
    from gantry import teams_send as _send

    page = getattr(_teams, "teams_page", None)
    importlib.reload(_common)
    importlib.reload(_dom)
    if page is not None:
        _dom.teams_page = page
    importlib.reload(_api)
    importlib.reload(_send)
    importlib.reload(_logic)
    importlib.reload(_mentions)
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

    async def _teams_reply(chat: str = "", text: str = "", force: bool = False,
                           message_id: str = "") -> dict:
        if not (text or "").strip():
            return {"ok": False, "error": "text 不能为空"}
        if bool(_cfg(ctx, "dry_run")) and not force:
            return {"ok": False, "error": "dry_run=true（加 force=true 才会真发）"}
        if not chat:
            # 当前会话：send_message 自身持有页面锁；没有会话标题可核验
            try:
                await teams.send_message(text)
                return {"ok": True, "chat": "(当前会话)", "sent": True}
            except Exception as exc:             # noqa: BLE001
                return {"ok": False, "error": str(exc)[:200]}
        # 打开 + 校验标题 + 输入/发送在同一个页面锁里完成，避免中途被轮询插队
        expected = {"peer": chat}
        if message_id:
            expected["message_id"] = message_id
        try:
            res = await teams.reply_to(chat, text, send=True, expected=expected,
                                       strict=False)
            ok = bool(res.get("sent"))
            return {"ok": ok, **res}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    async def _teams_probe() -> dict:
        return await teams.probe(dump_dir=_root(ctx) / "data" / "teams-probe")

    async def _unused_kb_search(query: str = "", limit: int = 4) -> dict:  # noqa: F841
        kb = _kb(ctx)
        try:
            kb.reindex()
        except Exception:                        # noqa: BLE001
            pass
        hits = _search(ctx, query, max(1, min(int(limit or 4), 10)))
        return {"query": query, "hits": hits,
                "filtered": pick_hits(hits, float(_cfg(ctx, "kb_min_score")))}

    async def _unused_kb_reindex() -> dict:  # noqa: F841
        return _kb(ctx).reindex(force=False)

    ctx.register_tool("teams_status", _teams_status,
                      "Teams 机器人状态（是否启用、知识库规模、最近一轮结果）", {})
    ctx.register_tool("teams_reply", _teams_reply,
                      "在 Teams 指定会话里发一条消息（dry_run 下需 force=true）",
                      {"chat": "会话标题片段（空=当前会话）", "text": "内容",
                       "force": "绕过 dry_run",
                       "message_id": "（可选）幂等键里的消息 id"})
    ctx.register_tool("teams_probe", _teams_probe,
                      "校准用：dump Teams 页面命中的选择器/会话/消息，写 data/teams-probe/", {})
    # 说明：kb_search / kb_reindex 由独立的 `kb` 插件提供（同一份 gantry.kb 实现），
    # 这里不再重复注册——重复注册会撞工具名，而且知识库是通用能力，不该绑在渠道插件里。

    # ── HTTP 接口（WebUI 面板/排障用）──
    async def _api_status(request=None):
        return bot.status()

    async def _api_answer(request=None):
        """只算不发：验证"收到问题会怎么回"（走混合检索 + kb_first）。"""
        q = ""
        try:
            params = getattr(request, "query_params", {}) or {}
            q = str(params.get("q") or params.get("question") or "")
        except Exception:                        # noqa: BLE001
            pass
        if not q.strip():
            return {"error": "需要 ?q=问题"}
        text, source = await bot.answer(q)
        return {"question": q, "text": text, "source": source,
                "would_reply": bool(text)}

    async def _api_run(request=None):
        return await bot.cycle()

    async def _api_reply(request):
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        return await _teams_reply(chat=str(body.get("chat") or ""),
                                  text=str(body.get("text") or ""),
                                  force=bool(body.get("force")),
                                  message_id=str(body.get("message_id") or ""))

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

    async def _api_send_api(request):
        """直接走 chatsvc API 发消息（绕 UI；显式调用才发）。"""
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        conv_id = str(body.get("conv_id") or "")
        text = str(body.get("text") or "")
        peer = str(body.get("peer") or "")
        snippet = str(body.get("snippet") or "")
        if not conv_id or not text:
            return {"error": "需要 conv_id 和 text"}
        if not (peer or str(body.get("topic") or "") or str(body.get("peer_guid") or "")):
            return {"error": "发送前核验需要 peer / topic / peer_guid 至少一项"}
        expected = {
            "conv_id": conv_id,
            "peer": peer,
            "peer_guid": str(body.get("peer_guid") or ""),
            "topic": str(body.get("topic") or ""),
            "snippet": snippet,
            "message_id": str(body.get("message_id") or ""),
        }
        page = await teams.teams_open()
        return await teams.api_send_message(page, conv_id, text, peer=peer,
                                            snippet=snippet, expected=expected)

    ctx.register_api("POST", "api/send-api", _api_send_api)

    async def _api_compose(request):
        """写侧演练：打开会话并输入文本；?send=1 才真的点发送（默认不点）。"""
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        peer = str(body.get("peer") or "")
        text = str(body.get("text") or "")
        snippet = str(body.get("snippet") or "")
        conv_id = str(body.get("conv_id") or "")
        send = bool(body.get("send"))
        if not peer or not text:
            return {"error": "需要 peer 和 text"}
        expected = {
            "conv_id": conv_id,
            "peer": peer,
            "peer_guid": str(body.get("peer_guid") or ""),
            "topic": str(body.get("topic") or ""),
            "snippet": snippet,
            "message_id": str(body.get("message_id") or ""),
        }
        return await teams.reply_to(peer, text, send=send, snippet=snippet,
                                    conv_id=conv_id, expected=expected,
                                    strict=bool(body.get("strict")))

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
        """会话监看视图（IndexedDB，只读）。支持 ?since=<epoch_ms> 做增量。"""
        since = 0.0
        try:
            q = getattr(request, "query_params", {}) or {}
            since = float(q.get("since") or 0)
        except Exception:                        # noqa: BLE001
            since = 0.0
        try:
            return await teams.api_idb_watch(since=since)
        except teams.TeamsAuthRequired as exc:
            # 之前这里抛出去变成 500；现在返回结构化结果，WebUI 能显示"需要登录"
            return {"ok": False, "auth": "expired", "error": str(exc)[:300],
                    "hint": "在自动化浏览器里登录一次 Teams，然后重试"}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "auth": "error", "error": str(exc)[:300]}

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

    async def _api_resolve(request=None):
        """只读诊断：按 conv_id 查 chatsvc 记录并做发送前身份核验，不发送。"""
        cid = ""
        peer = ""
        topic = ""
        snippet = ""
        try:
            q = getattr(request, "query_params", {}) or {}
            cid = str(q.get("id") or "")
            peer = str(q.get("peer") or "")
            topic = str(q.get("topic") or "")
            snippet = str(q.get("snippet") or "")
        except Exception:                        # noqa: BLE001
            pass
        if not cid:
            return {"error": "需要 ?id=conversation_id"}
        page = await teams.teams_open()
        token = await teams._ensure_skype_token(page)
        expected = {"conv_id": cid, "peer": peer, "topic": topic,
                    "snippet": snippet, "peer_guid": str(
                        (getattr(request, "query_params", {}) or {}).get("peer_guid") or "")}
        _cid, resolved = await teams._resolve_conversation_id(
            page, cid, peer, token, snippet, expected=expected)
        return {"conv_id": cid, "resolved": resolved, "page_url": page.url}

    async def _api_messages(request=None):
        """只读：按 conv_id 读会话消息（服务端真值，用于核对发送）。"""
        cid, limit = "", 20
        try:
            q = getattr(request, "query_params", {}) or {}
            cid = str(q.get("id") or "")
            limit = int(q.get("limit") or 20)
        except Exception:                        # noqa: BLE001
            pass
        if not cid:
            return {"ok": False, "error": "需要 ?id=conversation_id"}
        page = await teams.teams_open()
        return await teams.api_messages(page, cid, max(1, min(limit, 50)))

    async def _api_reload_teams(request=None):
        """只读：重新加载 Teams 页面并返回 probe（同步 IDB/DOM 用）。"""
        page = await teams.teams_open()
        try:
            await page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}
        await asyncio.sleep(4)
        return {"ok": True, "url": page.url}

    async def _api_tabs(request=None):
        """只读：查看 Teams 标签页，确认是否重复开标签。"""
        return await teams.teams_tabs()

    async def _api_all_tabs(request=None):
        """只读：列出浏览器里所有标签页（排障 Teams 打开卡住时用）。"""
        from gantry import bridge as b

        out = []
        try:
            pages = list(b.ctx.pages) if b.ctx is not None else []
        except Exception:                        # noqa: BLE001
            pages = []
        for i, p in enumerate(pages):
            try:
                if p.is_closed():
                    continue
                out.append({"index": i, "url": (p.url or "")[:160],
                            "title": ((await p.title()) or "")[:100]})
            except Exception:                    # noqa: BLE001
                continue
        return {"count": len(out), "tabs": out}

    async def _api_mentions(request=None):
        """只读：读取 Chat → Mentions 的当前列表（调试用；?gate=0 跳过未读 badge）。"""
        from gantry import teams
        gate = True
        try:
            q = getattr(request, "query_params", {}) or {}
            gate = str(q.get("gate", "1")).lower() not in ("0", "false", "no")
        except Exception:                        # noqa: BLE001
            gate = True
        try:
            return await teams.read_mentions(gate=gate)
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200]}

    async def _api_open_teams(request=None):
        """打开/聚焦 Teams 标签页并返回登录状态（不抛错；供人工过 MFA 用）。"""
        wait = 8.0
        try:
            body = await request.json()
            wait = float((body or {}).get("wait") or 8.0)
        except Exception:                        # noqa: BLE001
            pass
        try:
            # 直接取子模块实现：不依赖 façade 是否已随热重载更新
            from gantry.teams_dom import open_teams_for_login

            return await open_teams_for_login(wait=max(2.0, min(wait, 60.0)))
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}

    async def _api_tabs_cleanup(request=None):
        """关掉 Gantry 自开的多余 Teams 标签（不碰用户自己的 Chat 标签）。"""
        return await teams.close_extra_gantry_tabs()

    async def _api_settings(request):
        try:
            body = await request.json()
        except Exception:                        # noqa: BLE001
            body = {}
        overrides = dict(ctx.state.get("overrides") or {})
        for k in ("enabled", "dry_run", "send_enabled", "source", "chats",
                  "mention_tokens", "my_name", "my_user_id", "model", "interval_seconds",
                  "kb_min_score", "fallback_agent", "agent_mode", "only_direct",
                  "only_unread", "kb_synthesize", "typing_delay_ms",
                  "send_jitter_ms", "mentions_gate", "max_replies_per_cycle",
                  "send_timeout_seconds",
                  "prompt_with_kb", "prompt_no_kb", "unknown_text"):
            if k in body:
                overrides[k] = body[k]
        ctx.save_state(overrides=overrides)
        return {"ok": True, "overrides": overrides, "status": bot.status()}

    ctx.register_api("GET", "api/status", _api_status)
    ctx.register_api("POST", "api/run", _api_run)
    ctx.register_api("GET", "api/answer", _api_answer)
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
    ctx.register_api("GET", "api/mentions", _api_mentions)
    ctx.register_api("POST", "api/open-teams", _api_open_teams)
    ctx.register_api("GET", "api/resolve", _api_resolve)
    ctx.register_api("GET", "api/messages", _api_messages)
    ctx.register_api("POST", "api/reload-teams", _api_reload_teams)
    ctx.register_api("GET", "api/tabs", _api_tabs)
    ctx.register_api("GET", "api/all-tabs", _api_all_tabs)
    ctx.register_api("POST", "api/tabs/cleanup", _api_tabs_cleanup)
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
        bot._task = asyncio.get_running_loop().create_task(bot.loop())
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
    ob = getattr(ctx, "_teams_outbox", None)
    if ob is not None:
        try:
            ob.close()
        except Exception:                        # noqa: BLE001
            pass
