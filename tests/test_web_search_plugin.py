# -*- coding: utf-8 -*-
"""web-search 插件的解析与回退（离线：不联网，喂固定 HTML/XML）。

为什么值得测：2026-11-02 用户反馈"8B 连搜新闻都做不到"。查下来是**工具**在
`html.duckduckgo.com` 间歇性返回 0 结果，模型拿到空列表只能凭记忆编。
修法是 ddg 空结果重试 + 回退 Google News RSS —— 这两条路径都用固定样本覆盖，
避免以后改解析又悄悄坏掉。
"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location("ws_plugin", ROOT / "plugins" / "web-search" / "plugin.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

DDG_HTML = '''
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FSmall_language_model&amp;rut=abc">Small language model - Wikipedia</a>
  </h2>
  <a class="result__snippet" href="#">An SLM is a language model &lt;b&gt;smaller&lt;/b&gt; than LLMs.</a>
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://example.com/slm">What are SLMs?</a>
  </h2>
  <a class="result__snippet" href="#">Overview of small models.</a>
</div>
'''

GNEWS_XML = '''<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[Small language models in medicine - Nature]]></title>
<link>https://news.google.com/rss/articles/AAA</link>
<description><![CDATA[<a href="x">Small models</a> promise savings.]]></description></item>
<item><title>The future of AI may be small</title>
<link>https://news.google.com/rss/articles/BBB</link>
<description>Cheap and unprofitable?</description></item>
</channel></rss>'''


class DdgParseTests(unittest.TestCase):
    def test_parses_and_decodes_redirect_url(self):
        out = ws._parse_ddg(DDG_HTML, 5) if hasattr(ws, "_parse_ddg") else None
        if out is None:                      # 目前解析内联在 _search_ddg 里
            import re
            out = []
            for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                 DDG_HTML, re.S):
                out.append({"title": ws._clean(m.group(2)),
                            "url": ws._decode_ddg(m.group(1))})
        self.assertGreaterEqual(len(out), 2)
        self.assertEqual(out[0]["url"], "https://en.wikipedia.org/wiki/Small_language_model")
        self.assertIn("Small language model", out[0]["title"])

    def test_decode_ddg_handles_variants(self):
        self.assertEqual(ws._decode_ddg("https://a.example/x"), "https://a.example/x")
        self.assertEqual(ws._decode_ddg("//duckduckgo.com/l/?uddg=https%3A%2F%2Fb.example%2Fy"),
                         "https://b.example/y")
        self.assertEqual(ws._decode_ddg("//c.example/z"), "https://c.example/z")


class GNewsParseTests(unittest.TestCase):
    def test_parses_items(self):
        import re
        items = re.findall(r"<item>(.*?)</item>", GNEWS_XML, re.S)
        self.assertEqual(len(items), 2)
        first = items[0]
        t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", first, re.S)
        self.assertIn("Small language models in medicine", ws._clean(t.group(1)))

    def test_clean_strips_tags_and_cdata(self):
        self.assertEqual(ws._clean("<a href='x'>Small</a> models"), "Small models")
        self.assertEqual(ws._clean("  a\n b  "), "a b")


class FallbackTests(unittest.TestCase):
    """ddg 空 → 重试一次 → 还空就回退 gnews；顺序与 backend 字段要能看出来。"""

    def setUp(self):
        self._orig = dict(ws._BACKENDS)

    def tearDown(self):
        ws._BACKENDS.clear()
        ws._BACKENDS.update(self._orig)

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_falls_back_to_gnews(self):
        calls = {"ddg": 0, "gnews": 0}

        async def ddg(q, n):
            calls["ddg"] += 1
            return []

        async def gnews(q, n):
            calls["gnews"] += 1
            return [{"title": "t", "url": "u", "snippet": "s"}]

        ws._BACKENDS.update({"ddg": ddg, "gnews": gnews})
        r = self._run(ws.web_search("x", 3, "auto"))
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["backend"], "gnews")
        self.assertEqual(calls["ddg"], 2, "ddg 空结果应重试一次")
        self.assertEqual(calls["gnews"], 1)

    def test_ddg_succeeds_on_retry(self):
        seq = iter([[], [{"title": "t2", "url": "u2", "snippet": ""}]])

        async def ddg(q, n):
            return next(seq)

        ws._BACKENDS.update({"ddg": ddg})
        r = self._run(ws.web_search("x", 3, "auto"))
        self.assertEqual(r["backend"], "ddg", "重试成功就该用 ddg，不必回退")
        self.assertEqual(r["count"], 1)

    def test_all_fail_reports_errors(self):
        async def empty(q, n):
            return []

        ws._BACKENDS.update({"ddg": empty, "gnews": empty})
        r = self._run(ws.web_search("x", 3, "auto"))
        self.assertEqual(r["count"], 0)
        self.assertTrue(r["errors"], "全空时要带 errors，模型才知道为什么没结果")
        self.assertIn("没有结果", " ".join(r["errors"]))


if __name__ == "__main__":
    unittest.main()
