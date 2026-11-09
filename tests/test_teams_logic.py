# -*- coding: utf-8 -*-
"""Teams 自动化模块的"纯逻辑"测试（不碰浏览器）。

选择器/点击这类东西只能在真机校准（`gantry.teams.probe()`），但**去重键、@识别、
问题抽取、回答拼装**这些逻辑可以完全离线测——它们才是最容易出错、也最容易悄悄回归的部分。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gantry import teams  # noqa: E402
from gantry.teams_logic import (  # noqa: E402
    build_kb_answer, classify_chat, extract_question, has_mention, inherit_authors,
    is_direct_id, is_new, mention_names, normalise_text, peer_guid, pick_hits,
    should_answer, strip_leading_name, strip_mentions,
)


class MentionTests(unittest.TestCase):
    def test_plain_at_mention(self):
        self.assertTrue(has_mention("@Gantry 打印机连不上", ["@Gantry"]))
        self.assertTrue(has_mention("请问 @gantry 这个怎么弄", ["@Gantry"]))  # 大小写不敏感
        self.assertTrue(has_mention("＠Gantry 全角也能认", ["@Gantry"]))      # 全角 @

    def test_no_mention(self):
        self.assertFalse(has_mention("打印机连不上", ["@Gantry"]))
        self.assertFalse(has_mention("邮件里说 @all 也算但不在白名单", ["@Gantry"]))

    def test_multiple_tokens(self):
        self.assertTrue(has_mention("@IT小助手 帮我看下", ["@Gantry", "@IT小助手"]))

    def test_empty_tokens_never_match(self):
        self.assertFalse(has_mention("@Gantry 你好", []))
        self.assertFalse(has_mention("@Gantry 你好", [""]))


class MentionMetadataTests(unittest.TestCase):
    """Teams 的 @ 经常只在 `properties.mentions` 里，正文没有 "@名字"。"""

    def test_mention_text(self):
        self.assertEqual(mention_names([{"mentionText": "Alex Chen - Example"}]),
                         ["Alex Chen - Example"])

    def test_graph_shape(self):
        self.assertEqual(
            mention_names([{"mentioned": {"user": {"displayName": "Alex Chen - Example"}}}]),
            ["Alex Chen - Example"])

    def test_plain_display_name(self):
        self.assertEqual(mention_names([{"displayName": "Gantry"}]), ["Gantry"])

    def test_json_string_empty_is_not_a_mention(self):
        # Teams 有时把 properties.mentions 存成字符串 "[]"；不能当成一个名字
        self.assertEqual(mention_names("[]"), [])
        self.assertEqual(mention_names(""), [])

    def test_json_string_list_parsed(self):
        self.assertEqual(mention_names('[{"mentionText":"Alex Chen - Example"}]'),
                         ["Alex Chen - Example"])

    def test_text_without_at_still_detected_via_metadata(self):
        text = "Alex Chen - Example 1＋1 大于2吗？"
        self.assertFalse(has_mention(text, ["@Gantry"]))
        self.assertTrue(mention_names([{"mentionText": "Alex Chen - Example"}]))

    def test_strip_mention_name(self):
        q = strip_mentions("Alex Chen - Example 1＋1 大于2吗？", ["Alex Chen - Example"])
        self.assertEqual(q, "1＋1 大于2吗？")

    def test_strip_leading_name_when_mentions_missing(self):
        """mentions 元数据缺失时，正文开头的"@自己名字"也要剥掉。

        实测根因：content 里 @ 是 `<at>Alex Chen - Example</at>`，只删标签会把名字
        留在正文开头，导致问题和搜索片段都带 "Alex Chen - Example" 前缀。
        """
        self.assertEqual(
            strip_leading_name("Alex Chen - Example 1+1 大于2吗？", "Alex Chen - Example"),
            "1+1 大于2吗？")
        self.assertEqual(
            strip_leading_name("Alex Chen - Example：打印机脱机", "Alex Chen - Example"),
            "打印机脱机")
        # 不是开头的不动，避免误删正文
        self.assertEqual(
            strip_leading_name("请问 Alex Chen - Example 是谁", "Alex Chen - Example"),
            "请问 Alex Chen - Example 是谁")
        # 空名字原样返回
        self.assertEqual(strip_leading_name("abc", ""), "abc")


class ExtractTests(unittest.TestCase):
    def test_strips_mention_and_punctuation(self):
        q = extract_question("@Gantry 打印机脱机怎么处理？", ["@Gantry"])
        self.assertEqual(q, "打印机脱机怎么处理？")

    def test_keeps_body_when_mention_in_middle(self):
        q = extract_question("请问 @Gantry VPN 连不上怎么办", ["@Gantry"])
        self.assertIn("VPN 连不上怎么办", q)
        self.assertNotIn("@Gantry", q)

    def test_multi_line(self):
        q = extract_question("@Gantry\n第一行\n第二行", ["@Gantry"])
        self.assertIn("第一行", q)
        self.assertIn("第二行", q)

    def test_empty_after_strip(self):
        self.assertEqual(extract_question("@Gantry   ", ["@Gantry"]), "")

    def test_normalise(self):
        self.assertEqual(normalise_text("  a\n\n\n b  "), "a\n\nb")


class DedupeTests(unittest.TestCase):
    def test_key_stable(self):
        k1 = teams._msg_key("张三", "你好", 3)
        k2 = teams._msg_key("张三", "你好", 3)
        self.assertEqual(k1, k2)

    def test_key_differs(self):
        self.assertNotEqual(teams._msg_key("张三", "你好", 3),
                            teams._msg_key("张三", "你好", 4))
        self.assertNotEqual(teams._msg_key("张三", "你好", 3),
                            teams._msg_key("李四", "你好", 3))

    def test_is_new(self):
        seen = {"a"}
        self.assertFalse(is_new("a", seen))
        self.assertTrue(is_new("b", seen))


class KbAnswerTests(unittest.TestCase):
    def setUp(self):
        self.hits = [{
            "title": "VPN 连不上", "heading": "排查步骤", "tags": "vpn 网络",
            "path": "vpn.md", "score": 0.62,
            "text": "1. 确认家里宽带能打开门户网站。\n2. 公司 VPN 网关地址是 vpn.example.com。",
        }]

    def test_answer_has_source_and_body(self):
        ans = build_kb_answer("VPN 连不上", self.hits, max_chars=800)
        self.assertIn("《VPN 连不上》", ans)
        self.assertIn("排查步骤", ans)
        self.assertIn("vpn.example.com", ans)
        self.assertIn("知识库", ans)

    def test_multiple_hits(self):
        hits = self.hits + [dict(self.hits[0], title="WiFi 掉线", heading="处理",
                                 text="换 5GHz 频段。")]
        ans = build_kb_answer("网络", hits, max_chars=2000)
        self.assertIn("WiFi 掉线", ans)
        self.assertIn("VPN 连不上", ans)

    def test_truncates(self):
        big = [dict(self.hits[0], text="x" * 5000)]
        ans = build_kb_answer("x", big, max_chars=600)
        self.assertLessEqual(len(ans), 1400, "长文要截断，否则 Teams 里刷屏")

    def test_empty_hits(self):
        self.assertEqual(build_kb_answer("q", [], 800), "")


class ShouldAnswerTests(unittest.TestCase):
    """一条消息要不要回：四个理由必须都能区分（排障时看日志就知道为什么没回）。"""

    TOKENS = ["@Gantry"]

    def test_yes(self):
        ok, why = should_answer("@Gantry 打印机脱机了", self.TOKENS, [], "k1", mine=False)
        self.assertTrue(ok)
        self.assertIn("打印机脱机", why)

    def test_no_mention(self):
        ok, why = should_answer("打印机脱机了", self.TOKENS, [], "k1", mine=False)
        self.assertFalse(ok)
        self.assertEqual(why, "没有 @")

    def test_mine(self):
        ok, why = should_answer("@Gantry 你好", self.TOKENS, [], "k1", mine=True)
        self.assertFalse(ok)
        self.assertEqual(why, "自己发的")

    def test_already_seen(self):
        ok, why = should_answer("@Gantry 你好", self.TOKENS, ["k1"], "k1", mine=False)
        self.assertFalse(ok)
        self.assertEqual(why, "已回复过")

    def test_only_mention(self):
        ok, why = should_answer("@Gantry", self.TOKENS, [], "k1", mine=False)
        self.assertFalse(ok)
        self.assertEqual(why, "去掉 @ 后为空")


class PickHitsTests(unittest.TestCase):
    def test_threshold_and_order(self):
        hits = [{"title": "a", "score": 0.2}, {"title": "b", "score": 0.9},
                {"title": "c", "score": 0.5}]
        got = pick_hits(hits, 0.35)
        self.assertEqual([h["title"] for h in got], ["b", "c"])

    def test_all_below(self):
        self.assertEqual(pick_hits([{"title": "a", "score": 0.1}], 0.35), [])


class MeNameTests(unittest.TestCase):
    """me-control 的 aria-label 常带前缀，必须清洗后再比对，否则"自己发的"永远判不出来。"""

    def test_strip_account_prefix(self):
        from gantry.teams import _clean_me

        self.assertEqual(_clean_me("Account manager for Justin Jia"), "Justin Jia")
        self.assertEqual(_clean_me("Account manager of Justin Jia"), "Justin Jia")
        self.assertEqual(_clean_me("Account manager"), "")
        self.assertEqual(_clean_me("Settings and more"), "")
        self.assertEqual(_clean_me("  Justin   Jia  "), "Justin Jia")
        self.assertEqual(_clean_me("个人资料 Justin Jia"), "Justin Jia")

    def test_strip_profile_picture_prefix(self):
        """实测：me-control-avatar 的 aria-label 是 "Profile picture of Alex Chen - Example"。"""
        from gantry.teams import _clean_me

        self.assertEqual(_clean_me("Profile picture of Alex Chen - Example"),
                         "Alex Chen - Example")
        self.assertEqual(_clean_me("Profile photo of Lynn Ling - Example"),
                         "Lynn Ling - Example")
        self.assertEqual(_clean_me("头像 Alex Chen - Example"), "Alex Chen - Example")
        self.assertEqual(_clean_me("Profile picture"), "")

    def test_plain_name_kept(self):
        from gantry.teams import _clean_me

        self.assertEqual(_clean_me("Justin Jia - Example"), "Justin Jia - Example")

    def test_empty(self):
        from gantry.teams import _clean_me

        self.assertEqual(_clean_me(""), "")
        self.assertEqual(_clean_me(None or ""), "")


class InheritAuthorsTests(unittest.TestCase):
    """真实场景（2026-11-02，SDWAN 群聊最后 6 条）：只有连续发言的第一条带名字。

    用户确认：6 条里**只有 4 条**是 Justin 的（"I think we can mark it as completed"
    到 "I think that's all for today"）。也就是说前 2 条是别人发的、且没有作者节点，
    必须保持"未知"而不是继承成 Justin。
    """

    def test_run_inherits_only_after_first_name(self):
        raw = ["", "", "Justin Jia - Example", "", "", ""]
        got = inherit_authors(raw)
        self.assertEqual(got, ["", "", "Justin Jia - Example", "Justin Jia - Example",
                               "Justin Jia - Example", "Justin Jia - Example"])

    def test_leading_unknown_stays_unknown(self):
        got = inherit_authors(["", "Piotr Wierzbicki - Example", ""])
        self.assertEqual(got, ["", "Piotr Wierzbicki - Example", "Piotr Wierzbicki - Example"])

    def test_switch_between_people(self):
        raw = ["A", "", "B", "", ""]
        self.assertEqual(inherit_authors(raw), ["A", "A", "B", "B", "B"])

    def test_empty_input(self):
        self.assertEqual(inherit_authors([]), [])
        self.assertEqual(inherit_authors(["", ""]), ["", ""])


class ChatKindTests(unittest.TestCase):
    def test_direct_is_two(self):
        self.assertEqual(classify_chat(2), "direct")

    def test_group(self):
        self.assertEqual(classify_chat(3), "group")
        self.assertEqual(classify_chat(19), "group")

    def test_unknown(self):
        self.assertEqual(classify_chat(None), "unknown")
        self.assertEqual(classify_chat(0), "unknown")
        self.assertEqual(classify_chat("x"), "unknown")
        self.assertEqual(classify_chat("2"), "direct")


TENANT = "00000000-0000-0000-0000-000000000001"
PEER = "fd41f5b9-3975-446f-b1c4-8126101f98ec"


class DirectIdTests(unittest.TestCase):
    """私聊 = `19:<guid>_<guid>@unq.gbl.spaces`；群聊 = `19:<opaque>@thread.v2|tacv2`。

    实测教训：`memberProperties` 是**单个**成员对象（键固定 relationshipState/role），
    按它的键数判断会把所有会话都算成 2 人（351 条里 289 条被误判成私聊）。
    """

    def test_direct_two_guids(self):
        self.assertTrue(is_direct_id(f"19:{TENANT}_{PEER}@unq.gbl.spaces"))
        self.assertTrue(is_direct_id(f"19:{PEER}_{TENANT}@unq.gbl.spaces"))

    def test_group_threads(self):
        self.assertFalse(is_direct_id("19:d5449eaae3cf4c1cab56706f08519a3f@thread.v2"))
        self.assertFalse(is_direct_id(
            "19:pK-n3Doud2MNk6feV68OiVvL6vfySjtlepDKKc0WNao1@thread.tacv2"))
        self.assertFalse(is_direct_id("19:meeting_NzAx@thread.v2"))

    def test_other_forms(self):
        self.assertFalse(is_direct_id(""))
        self.assertFalse(is_direct_id("8:orgid:" + PEER))
        self.assertFalse(is_direct_id("48:notes"))

    def test_peer_guid(self):
        self.assertEqual(peer_guid(f"19:{TENANT}_{PEER}@unq.gbl.spaces", TENANT), PEER)
        self.assertEqual(peer_guid(f"19:{PEER}_{TENANT}@unq.gbl.spaces", TENANT), PEER)

    def test_peer_guid_without_tenant_still_returns_something(self):
        got = peer_guid(f"19:{TENANT}_{PEER}@unq.gbl.spaces")
        self.assertIn(got, (TENANT, PEER))

    def test_peer_guid_group_empty(self):
        self.assertEqual(peer_guid("19:abc@thread.v2", TENANT), "")


class CoverageGateTests(unittest.TestCase):
    """混合检索分数高但词面无关时不能当命中（P1 测试里"P1 队列测试"命中了 wifi 文档）。"""

    def test_terms_split(self):
        from gantry.teams_logic import query_terms

        terms = query_terms("VPN 连不上怎么办")
        self.assertIn("vpn", terms)
        self.assertTrue(any("连不" in t for t in terms), terms)
        self.assertNotIn("怎么办", terms, "停用词应被去掉")

    def test_coverage_unrelated_is_zero(self):
        from gantry.teams_logic import coverage

        hit = {"title": "wifi", "heading": "Wi-Fi 802.1X 认证失败",
               "text": "一直重输密码没用，多半是证书或时间问题"}
        self.assertLess(coverage("P1 队列测试，收到请忽略", hit), 0.34)

    def test_coverage_relevant_is_high(self):
        from gantry.teams_logic import coverage

        hit = {"title": "VPN 连不上怎么办", "heading": "排查步骤",
               "text": "换手机热点试一次，端口 443"}
        self.assertGreaterEqual(coverage("VPN 连不上怎么办", hit), 0.5)

    def test_pick_hits_drops_unrelated(self):
        from gantry.teams_logic import pick_hits

        hits = [{"title": "wifi", "heading": "Wi-Fi", "text": "证书问题",
                 "score": 0.62}]
        self.assertEqual(pick_hits(hits, 0.5, "P1 队列测试，收到请忽略", 0.34), [])
        self.assertEqual(len(pick_hits(hits, 0.5)), 1, "只给阈值时保持旧行为")

    def test_pick_hits_keeps_relevant(self):
        from gantry.teams_logic import pick_hits

        hits = [{"title": "VPN 连不上怎么办", "heading": "排查",
                 "text": "换热点、端口 443", "score": 0.62}]
        self.assertEqual(len(pick_hits(hits, 0.5, "VPN 连不上怎么办", 0.34)), 1)


if __name__ == "__main__":
    unittest.main()
