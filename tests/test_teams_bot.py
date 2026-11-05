# -*- coding: utf-8 -*-
"""teams-bot 插件测试（离线）：读侧走 IndexedDB（假数据），写侧默认关闭。

覆盖：
  1. enabled=false / dry_run=true 时绝不动手；
  2. 只处理**私聊**（id 形态 19:<guid>_<guid>@unq.gbl.spaces），群聊一律跳过；
  3. 只回应含 @ 且不是自己发的、没回过的新消息（按 lastId 去重）；
  4. 知识库命中即答；未命中才走模型/兜底文案；
  5. send_enabled 默认 false → 只写 would[]；打开后才真调 send_message。
"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import teams as teams_mod  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "teams_bot_plugin", ROOT / "plugins" / "teams-bot" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

TENANT = "00000000-0000-0000-0000-000000000001"
PEER_A = "fd41f5b9-3975-446f-b1c4-8126101f98ec"
PEER_B = "954a61a6-00f0-43a9-a494-879d173c1e8f"


def dm(peer: str = PEER_A) -> str:
    return f"19:{TENANT}_{peer}@unq.gbl.spaces"


def conv(cid: str, text: str, frm: str = "Lynn Ling - Example", last_id: str = "m1",
         peer: str = "Lynn Ling - Example", unread: bool = True) -> dict:
    return {"id": cid, "isDirect": "@unq.gbl.spaces" in cid, "peerName": peer,
            "lastText": text, "lastFrom": frm, "lastAt": "2026-11-02T09:42:09Z",
            "lastId": last_id, "lastTime": 1793613729, "unread": unread}


class FakeCtx:
    def __init__(self, **config):
        self.config = dict(config)
        self.state: dict = {}
        self.logs: list[str] = []
        self.tools: list[tuple] = []
        self.apis: list[tuple] = []
        self.panels: list[dict] = []

    def log(self, msg, *a, **k):
        self.logs.append(str(msg))

    def save_state(self, **kw):
        self.state.update(kw)

    def register_tool(self, name, fn, desc, args=None, **k):
        self.tools.append((name, fn, desc))

    def register_api(self, method, path, fn):
        self.apis.append((method, path, fn))

    def register_panel(self, panel):
        self.panels.append(panel)


class FakeKB:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.reindexed = 0

    def reindex(self, force=False):
        self.reindexed += 1
        return {"added": 0, "updated": 0, "skipped": 1, "removed": 0, "fts": True,
                "documents": 1, "chunks": 1}

    def search(self, query, limit=4):
        return list(self.hits)[:limit]

    def stats(self):
        return {"documents": 1, "chunks": 1, "chars": 100, "fts": True}

    def close(self):
        pass


class FakeTeams:
    def __init__(self, conversations=None):
        self.conversations = conversations or []
        self.sent: list[str] = []
        self.opened: list[str] = []
        self.replied: list[tuple] = []
        self.last_expected = None
        self.last_strict = None

    async def reply_to(self, peer, text, send=False, snippet="", conv_id="", **kwargs):
        """写侧入口：记录调用；send=True 时模拟发送成功。"""
        self.replied.append((peer, text, send, snippet))
        self.last_expected = kwargs.get("expected")
        self.last_strict = kwargs.get("strict")
        if not send:
            return {"opened": True, "sent": False, "sendButton": True}
        self.sent.append(text)
        return {"opened": True, "sent": True, "how": "snippet"}

    async def api_idb_watch(self, page=None, limit=400, since=0.0):
        self.since_calls = getattr(self, 'since_calls', [])
        self.since_calls.append(since)
        c = list(self.conversations)
        return {"total": len(c), "direct": sum(1 for x in c if x.get("isDirect")),
                "recent": c[:6], "directSample": [x for x in c if x.get("isDirect")][:6]}

    async def open_chat(self, target, scroll_pages=8):
        self.opened.append(str(target))
        return True

    async def send_message(self, text):
        self.sent.append(text)


class BotTestCase(unittest.TestCase):
    def setUp(self):
        names = ("api_idb_watch", "open_chat", "send_message", "reply_to")
        self._orig = {n: getattr(teams_mod, n) for n in names}
        self.fake = FakeTeams()
        for n in names:
            setattr(teams_mod, n, getattr(self.fake, n))
        self._orig_kb = plugin._kb
        self.kb = FakeKB()
        plugin._kb = lambda ctx: self.kb

    def tearDown(self):
        for n, fn in self._orig.items():
            setattr(teams_mod, n, fn)
        plugin._kb = self._orig_kb

    def run_cycle(self, **cfg):
        cfg.setdefault("enabled", True)
        cfg.setdefault("mention_tokens", ["@Gantry"])
        cfg.setdefault("outbox_path", ":memory:")
        cfg.setdefault("kb_synthesize", False)   # 单测不调模型；synthesis 单独测
        bot = plugin.Bot(FakeCtx(**cfg))

        async def _run():
            out = await bot.cycle()
            await bot.drain()                    # P1：发送改队列，等它发完再断言
            return out

        self.bot = bot
        return asyncio.new_event_loop().run_until_complete(_run())


class GuardTests(BotTestCase):
    def test_disabled_does_nothing(self):
        self.fake.conversations = [conv(dm(), "@Gantry VPN 连不上")]
        out = self.run_cycle(enabled=False)
        self.assertEqual(out.get("skipped"), "disabled")
        self.assertEqual(self.fake.sent, [])

    def test_dry_run_records_but_does_not_send(self):
        self.fake.conversations = [conv(dm(), "@Gantry VPN 连不上")]
        self.kb.hits = [{"title": "VPN 连不上怎么办", "heading": "排查步骤", "score": 0.9,
                         "path": "vpn.md", "text": "换热点试试。"}]
        out = self.run_cycle(dry_run=True)
        self.assertEqual(self.fake.sent, [], "dry_run 下不许发消息")
        self.assertEqual(len(out["would"]), 1)
        self.assertIn("kb:VPN", out["would"][0]["source"])
        self.assertIn("换热点试试", out["would"][0]["text"])


class DirectTests(BotTestCase):
    def test_group_chat_ignored(self):
        self.fake.conversations = [
            conv("19:pK-n3Doud2MNk6feV68OiVvL6vfySjtlepDKKc0WNao1@thread.tacv2",
                 "@Gantry 群里问一下", last_id="g1"),
            conv(dm(), "@Gantry 私聊问题", last_id="d1"),
        ]
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        out = self.run_cycle(dry_run=True)
        self.assertEqual(out["scanned"], 1, "群聊不该进扫描列表")
        self.assertEqual(len(out["would"]), 1)
        self.assertEqual(out["would"][0]["chat"], "Lynn Ling - Example")

    def test_own_message_ignored(self):
        self.fake.conversations = [conv(dm(), "@Gantry 我自己写的", frm="Alex Chen - Example")]
        out = self.run_cycle(dry_run=True, my_name="Alex Chen - Example")
        self.assertEqual(out["would"], [])
        self.assertTrue(any("自己发的" in str(s.get("why")) for s in out["skipped"]))

    def test_no_mention_ignored(self):
        self.fake.conversations = [conv(dm(), "打印机坏了")]
        out = self.run_cycle(dry_run=True)
        self.assertEqual(out["would"], [])

    def test_read_conversation_ignored(self):
        """only_unread 默认开启：已读会话的历史消息不能回。"""
        self.fake.conversations = [conv(dm(), "@Gantry 旧消息", unread=False)]
        out = self.run_cycle(dry_run=True)
        self.assertEqual(out["would"], [])
        self.assertTrue(any(s.get("why") == "已读" for s in out["skipped"]))

    def test_read_but_new_lastid_still_processed(self):
        """会话开着被自动标已读，但 lastId 变了 = 新消息，仍要回。"""
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 旧消息", last_id="old", unread=False)]
        ctx = FakeCtx(enabled=True, dry_run=True, mention_tokens=["@Gantry"],
                      kb_synthesize=False)
        bot = plugin.Bot(ctx)
        loop = asyncio.new_event_loop()
        first = loop.run_until_complete(bot.cycle())     # 首次基线：已读跳过
        self.assertEqual(first["would"], [])
        self.fake.conversations = [conv(dm(), "@Gantry 新消息", last_id="new", unread=False)]
        second = loop.run_until_complete(bot.cycle())    # lastId 变了 → 要回
        self.assertEqual(len(second["would"]), 1)
        self.assertEqual(second["would"][0]["question"], "新消息")

    def test_dedupe_same_last_id(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 问题", last_id="same")]
        ctx = FakeCtx(enabled=True, dry_run=True, mention_tokens=["@Gantry"],
                      kb_synthesize=False)
        bot = plugin.Bot(ctx)
        loop = asyncio.new_event_loop()
        first = loop.run_until_complete(bot.cycle())
        second = loop.run_until_complete(bot.cycle())
        self.assertEqual(len(first["would"]), 1)
        self.assertEqual(second["would"], [], "同一条消息不该回两次")

    def test_non_chat_last_message_ignored(self):
        """最后一条是系统事件（加成员等）时不能拿来回答。"""
        c = conv(dm(), "@Gantry 问题")
        c["lastType"] = "ThreadActivity/AddMember"
        self.fake.conversations = [c]
        out = self.run_cycle(dry_run=True)
        self.assertEqual(out["would"], [])
        self.assertTrue(any("非聊天消息" in str(s.get("why"))
                            for s in out["skipped"]))

    def test_max_replies_per_cycle(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [
            conv(dm(PEER_A), "@Gantry 问题一", last_id="a"),
            conv(dm(PEER_B), "@Gantry 问题二", last_id="b"),
        ]
        out = self.run_cycle(dry_run=True, max_replies_per_cycle=1)
        self.assertEqual(len(out["would"]), 1)


class AnswerTests(BotTestCase):
    def test_kb_hit_source(self):
        self.kb.hits = [{"title": "VPN 连不上", "heading": "排查", "score": 0.7,
                         "path": "vpn.md", "text": "换热点。"}]
        bot = plugin.Bot(FakeCtx(kb_synthesize=False))
        text, source = asyncio.new_event_loop().run_until_complete(bot.answer("VPN 连不上"))
        self.assertTrue(source.startswith("kb:"))
        self.assertIn("换热点", text)

    def test_kb_hit_uses_model_synthesis(self):
        """命中文档时应让模型组织回答，而不是整段抄资料。"""
        self.kb.hits = [{"title": "GD 问题", "heading": "排查", "score": 0.8,
                         "path": "gd.md", "text": "先检查网络，再重启客户端。"}]
        called = {}

        async def fake_answer(self_, question, hits=None):
            called["hits"] = hits
            return "先确认网络，然后重启 GD 客户端。"

        orig = plugin.Bot._agent_answer
        plugin.Bot._agent_answer = fake_answer
        try:
            bot = plugin.Bot(FakeCtx(kb_synthesize=True, fallback_agent=True))
            text, source = asyncio.new_event_loop().run_until_complete(
                bot.answer("我 GD 用不了了"))
        finally:
            plugin.Bot._agent_answer = orig
        self.assertTrue(text.startswith("先确认网络"))
        self.assertIn("来源：", text)
        self.assertTrue(source.startswith("model+kb:"))
        self.assertTrue(called["hits"])

    def test_unknown_without_agent(self):
        self.fake.conversations = [conv(dm(), "@Gantry 火星怎么去")]
        out = self.run_cycle(dry_run=True, fallback_agent=False, reply_when_unknown=True)
        self.assertEqual(len(out["would"]), 1)
        self.assertIn("知识库里暂时没有", out["would"][0]["text"])

    def test_unknown_silent(self):
        self.fake.conversations = [conv(dm(), "@Gantry 火星怎么去")]
        out = self.run_cycle(dry_run=True, fallback_agent=False, reply_when_unknown=False)
        self.assertEqual(out["would"], [])

    def test_below_threshold_falls_back_to_model(self):
        self.kb.hits = [{"title": "无关", "heading": "", "score": 0.01, "path": "x.md",
                         "text": "无关"}]
        called = {}

        async def fake_answer(self_, question, hits=None):
            called["q"] = question
            return "模型给的答案"

        orig = plugin.Bot._agent_answer
        plugin.Bot._agent_answer = fake_answer
        try:
            self.fake.conversations = [conv(dm(), "@Gantry 这个怎么弄")]
            out = self.run_cycle(dry_run=True, fallback_agent=True)
        finally:
            plugin.Bot._agent_answer = orig
        self.assertEqual(out["would"][0]["text"], "模型给的答案")
        self.assertEqual(out["would"][0]["source"], "model")
        self.assertEqual(called["q"], "这个怎么弄")


class SendGatingTests(BotTestCase):
    def test_send_disabled_by_default(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 问题")]
        out = self.run_cycle(dry_run=False)
        self.assertEqual(self.fake.sent, [], "send_enabled=false 时不许发")
        self.assertEqual(out["replied"], 0)

    def test_send_when_both_switches_on(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 问题")]
        out = self.run_cycle(dry_run=False, send_enabled=True)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertEqual(out["queued"], 1)
        self.assertEqual(out["would"][0]["sent"], True)
        # 写侧要求：带上会话名 + 最后一条消息片段（用于按消息搜索定位会话）
        peer, text, send, snippet = self.fake.replied[0]
        self.assertEqual(peer, "Lynn Ling - Example")
        self.assertTrue(send)
        self.assertIn("Gantry", snippet)
        # P0：发送前必须带上完整身份核验上下文，且严格模式不许回退 UI
        self.assertEqual(self.fake.last_expected["conv_id"], dm())
        self.assertEqual(self.fake.last_expected["peer"], "Lynn Ling - Example")
        self.assertTrue(self.fake.last_strict)
        self.assertEqual(self.fake.last_expected["message_id"], "m1")


class ConcurrencyAndIdempotencyTests(BotTestCase):
    def test_cycle_is_not_reentrant(self):
        bot = plugin.Bot(FakeCtx(enabled=True, outbox_path=":memory:"))
        loop = asyncio.new_event_loop()
        loop.run_until_complete(bot._lock.acquire())
        try:
            out = loop.run_until_complete(bot.cycle())
        finally:
            bot._lock.release()
        self.assertEqual(out.get("skipped"), "busy")

    def test_duplicate_after_restart_is_not_sent_again(self):
        import tempfile

        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 问题", last_id="same-msg")]
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "sends.db")
            cfg = dict(enabled=True, dry_run=False, send_enabled=True,
                       mention_tokens=["@Gantry"], outbox_path=path,
                       kb_synthesize=False)
            loop = asyncio.new_event_loop()
            ctx1, ctx2 = FakeCtx(**cfg), FakeCtx(**cfg)
            bot1, bot2 = plugin.Bot(ctx1), plugin.Bot(ctx2)
            try:
                async def _cycle(bot):
                    out = await bot.cycle()
                    await bot.drain()            # P1：发送是队列，等发完
                    return out

                first = loop.run_until_complete(_cycle(bot1))
                self.assertEqual(first["queued"], 1)
                self.assertEqual(len(self.fake.sent), 1)
                # 模拟重启：新 Bot、新 seen，但幂等箱还在
                second = loop.run_until_complete(_cycle(bot2))
            finally:
                for c in (ctx1, ctx2):
                    ob = getattr(c, "_teams_outbox", None)
                    if ob is not None:
                        ob.close()
            self.assertEqual(second["queued"], 0)
            self.assertEqual(len(self.fake.sent), 1, "重启后不能重复发送同一条")
            self.assertTrue(any(s.get("why") == "duplicate"
                                for s in second.get("skipped", [])))


class OverrideTests(BotTestCase):
    def test_override_beats_config(self):
        ctx = FakeCtx(enabled=False, dry_run=True)
        ctx.state["overrides"] = {"enabled": True, "dry_run": False}
        self.assertTrue(plugin._cfg(ctx, "enabled"))
        self.assertFalse(plugin._cfg(ctx, "dry_run"))

    def test_false_override_is_not_ignored(self):
        ctx = FakeCtx(enabled=True)
        ctx.state["overrides"] = {"enabled": False}
        self.assertFalse(plugin._cfg(ctx, "enabled"))

    def test_defaults(self):
        ctx = FakeCtx()
        self.assertEqual(plugin._cfg(ctx, "interval_seconds"), 30)
        self.assertFalse(plugin._cfg(ctx, "enabled"))
        self.assertTrue(plugin._cfg(ctx, "dry_run"))
        self.assertFalse(plugin._cfg(ctx, "send_enabled"))
        self.assertEqual(plugin._cfg(ctx, "source"), "idb")


class SetupTests(BotTestCase):
    def test_registers_tools_and_api(self):
        ctx = FakeCtx()
        plugin.setup(ctx)
        names = {t[0] for t in ctx.tools}
        for need in ("teams_status", "teams_reply", "teams_probe"):
            self.assertIn(need, names)
        # kb_search/kb_reindex 已迁到独立 kb 插件，这里不该再注册（会撞工具名）
        self.assertNotIn("kb_search", names)
        paths = {p for _, p, _ in ctx.apis}
        for need in ("api/status", "api/probe"):
            self.assertIn(need, paths)
        plugin.teardown(ctx)

    def test_reply_tool_respects_dry_run(self):
        ctx = FakeCtx(dry_run=True)
        plugin.setup(ctx)
        fn = dict((n, f) for n, f, _ in ctx.tools)["teams_reply"]
        res = asyncio.new_event_loop().run_until_complete(fn(chat="", text="hi"))
        self.assertFalse(res["ok"])
        self.assertIn("dry_run", res["error"])
        plugin.teardown(ctx)


class BotRetrievalTests(BotTestCase):
    """answer() 的知识库检索：有向量索引 → hybrid；没有 → FTS。"""

    class _FakeSem:
        def __init__(self, hits):
            self.hits = hits
            self.built = 0

        def stats(self):
            return {"vectorised": 1, "chunks": 1}

        def build(self, force=False):
            self.built += 1
            return {"vectorised": 1, "chunks": 1}

        def hybrid(self, q, limit, **kw):
            return list(self.hits)

    def _answer(self, question, sem):
        ctx = FakeCtx(enabled=True, kb_first=True, fallback_agent=False,
                      reply_when_unknown=False)
        ctx._sem = sem
        bot = plugin.Bot(ctx)
        return asyncio.new_event_loop().run_until_complete(bot.answer(question))

    def test_uses_hybrid_when_available(self):
        hits = [{"title": "VPN", "heading": "排查", "path": "vpn.md",
                 "text": "换热点试一次", "score": 0.9, "via": "vector"}]
        self.kb.hits = []                        # FTS 若被用到就没有结果
        text, source = self._answer("@Gantry VPN 连不上", self._FakeSem(hits))
        self.assertTrue(text)
        self.assertIn("VPN", source)

    def test_falls_back_to_fts_without_vector(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        text, source = self._answer("@Gantry 问题", None)
        self.assertTrue(text)
        self.assertIn("kb:", source)

    def test_hybrid_empty_falls_back_to_fts(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        text, source = self._answer("@Gantry 问题", self._FakeSem([]))
        self.assertTrue(text, "混合检索空结果时应退回 FTS")


class WatermarkAndAuthTests(BotTestCase):
    """P2：增量扫描传水位；P0：未登录时退避而不是每轮硬撞。"""

    def test_cycle_passes_watermark(self):
        self.fake.conversations = [conv(dm(), "@Gantry 问题")]
        self.kb.hits = []
        self.run_cycle(dry_run=True, fallback_agent=False, reply_when_unknown=False)
        self.assertEqual(self.fake.since_calls[0], 0.0, "首轮应全量")
        self.assertEqual(float(self.bot.ctx.state.get("idb_watermark") or 0),
                         1793613729, "首轮后应把最大 lastTime 记为水位")

    def test_second_cycle_uses_watermark(self):
        self.fake.conversations = [conv(dm(), "@Gantry 问题")]
        self.kb.hits = []
        ctx = FakeCtx(enabled=True, mention_tokens=["@Gantry"], dry_run=True,
                      outbox_path=":memory:", kb_synthesize=False)
        bot = plugin.Bot(ctx)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bot.cycle())
            loop.run_until_complete(bot.cycle())
        finally:
            pass
        self.assertEqual(self.fake.since_calls[0], 0.0)
        self.assertEqual(self.fake.since_calls[1], 1793613729, "第二轮应带上水位")

    def test_auth_required_backs_off(self):
        import gantry.teams as teams_mod

        async def boom(*a, **k):
            raise teams_mod.TeamsAuthRequired("Teams 要求登录")

        orig = teams_mod.api_idb_watch
        teams_mod.api_idb_watch = boom
        ctx = FakeCtx(enabled=True, mention_tokens=["@Gantry"], dry_run=True,
                      outbox_path=":memory:", kb_synthesize=False,
                      auth_backoff_seconds=300)
        bot = plugin.Bot(ctx)
        loop = asyncio.new_event_loop()
        try:
            out1 = loop.run_until_complete(bot.cycle())
            self.assertTrue(any("未登录" in e for e in out1.get("errors") or []))
            self.assertEqual(bot.status()["session"]["auth"], "expired")
            out2 = loop.run_until_complete(bot.cycle())
        finally:
            teams_mod.api_idb_watch = orig
        self.assertEqual(out2.get("skipped"), "auth_backoff")
        self.assertGreater(out2.get("retry_in") or 0, 0)


class AnswerTimeoutTests(BotTestCase):
    def test_answer_timeout_returns_fallback(self):
        async def slow(_self, _q):
            await asyncio.sleep(5)
            return ("慢答案", "model")

        orig = plugin.Bot._answer_inner
        plugin.Bot._answer_inner = slow
        try:
            ctx = FakeCtx(enabled=True, mention_tokens=["@Gantry"], dry_run=True,
                          outbox_path=":memory:", kb_synthesize=False,
                          answer_timeout_seconds=0.05, reply_when_unknown=True,
                          fallback_agent=True)
            bot = plugin.Bot(ctx)
            text, source = asyncio.new_event_loop().run_until_complete(
                bot.answer("@Gantry 慢问题"))
        finally:
            plugin.Bot._answer_inner = orig
        self.assertEqual(source, "timeout")
        self.assertTrue(text)


if __name__ == "__main__":
    unittest.main()
