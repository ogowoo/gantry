# -*- coding: utf-8 -*-
"""可插拔 reranker：配置、HTTP 协议解析与失败降级。"""
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import kb_rerank as r


class FakeReranker:
    name = "fake"
    top_k = 2

    def __init__(self):
        self.seen = None

    def rerank(self, query, hits, top_k=0):
        self.seen = (query, [h.get("parent_text") or h.get("text") for h in hits])
        return list(reversed(hits))[:int(top_k or self.top_k)]


class RerankConfigTests(unittest.TestCase):
    def test_disabled(self):
        self.assertIsNone(r.build_reranker({"enabled": False}))
        self.assertIsNone(r.build_reranker({}))

    def test_http_requires_url_and_model(self):
        with self.assertRaises(r.RerankError):
            r.build_reranker({"enabled": True, "base_url": "http://x"})

    def test_http_build(self):
        rr = r.build_reranker({"enabled": True, "base_url": "http://127.0.0.1:1/v1",
                               "model": "bge-reranker", "top_k": 3})
        self.assertIsInstance(rr, r.HttpReranker)
        self.assertEqual(rr._endpoint(), "http://127.0.0.1:1/v1/rerank")


class HttpRerankTests(unittest.TestCase):
    def _resp(self, payload):
        body = json.dumps(payload).encode()
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return resp

    def test_parses_results_and_sorts(self):
        rr = r.HttpReranker("http://x/v1", "m", top_k=2)
        hits = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
        payload = {"results": [{"index": 0, "relevance_score": 0.2},
                               {"index": 2, "relevance_score": 0.9},
                               {"index": 1, "relevance_score": 0.5}]}
        with mock.patch.object(r.urllib.request, "urlopen", return_value=self._resp(payload)) as u:
            out = rr.rerank("q", hits, top_k=2)
        self.assertEqual([x["text"] for x in out], ["c", "b"])
        self.assertEqual(out[0]["rerank_score"], 0.9)
        self.assertIn("/v1/rerank", u.call_args[0][0].full_url)

    def test_bad_response_raises(self):
        rr = r.HttpReranker("http://x/v1", "m")
        with mock.patch.object(r.urllib.request, "urlopen", return_value=self._resp({"oops": 1})):
            with self.assertRaises(r.RerankError):
                rr.rerank("q", [{"text": "a"}])

    def test_parent_text_is_preferred_for_documents(self):
        rr = r.HttpReranker("http://x/v1", "m", top_k=1)
        hits = [{"text": "child", "parent_text": "parent context"}]
        payload = {"data": [{"index": 0, "score": 0.8}]}
        with mock.patch.object(r.urllib.request, "urlopen", return_value=self._resp(payload)) as u:
            rr.rerank("q", hits)
        sent = json.loads(u.call_args[0][0].data.decode())
        self.assertEqual(sent["documents"], ["parent context"])


if __name__ == "__main__":
    unittest.main()
