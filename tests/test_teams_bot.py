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
         peer: str = "Lynn Ling - Example") -> dict:
    return {"id": cid, "isDirect": "@unq.gbl.spaces" in cid, "peerName": peer,
            "lastText": text, "lastFrom": frm, "lastAt": "2026-11-02T09:42:09Z",
            "lastId": last_id, "lastTime": 1793613729}


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

    async def reply_to(self, peer, text, send=False, snippet=""):
        """写侧入口：记录调用；send=True 时模拟发送成功。"""
        self.replied.append((peer, text, send, snippet))
        if not send:
            return {"opened": True, "sent": False, "sendButton": True}
        self.sent.append(text)
        return {"opened": True, "sent": True, "how": "snippet"}

    async def api_idb_watch(self, page=None, limit=400):
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
        bot = plugin.Bot(FakeCtx(**cfg))
        return asyncio.new_event_loop().run_until_complete(bot.cycle())


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

    def test_dedupe_same_last_id(self):
        self.kb.hits = [{"title": "T", "heading": "", "score": 0.9, "path": "t.md",
                         "text": "答案"}]
        self.fake.conversations = [conv(dm(), "@Gantry 问题", last_id="same")]
        ctx = FakeCtx(enabled=True, dry_run=True, mention_tokens=["@Gantry"])
        bot = plugin.Bot(ctx)
        loop = asyncio.new_event_loop()
        first = loop.run_until_complete(bot.cycle())
        second = loop.run_until_complete(bot.cycle())
        self.assertEqual(len(first["would"]), 1)
        self.assertEqual(second["would"], [], "同一条消息不该回两次")

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
        bot = plugin.Bot(FakeCtx())
        text, source = asyncio.new_event_loop().run_until_complete(bot.answer("VPN 连不上"))
        self.assertTrue(source.startswith("kb:"))
        self.assertIn("换热点", text)

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
        self.assertEqual(out["replied"], 1)
        # 写侧要求：带上会话名 + 最后一条消息片段（用于按消息搜索定位会话）
        peer, text, send, snippet = self.fake.replied[0]
        self.assertEqual(peer, "Lynn Ling - Example")
        self.assertTrue(send)
        self.assertIn("Gantry", snippet)


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
        for need in ("teams_status", "teams_reply", "kb_search", "teams_probe"):
            self.assertIn(need, names)
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


if __name__ == "__main__":
    unittest.main()
