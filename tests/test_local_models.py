# -*- coding: utf-8 -*-
"""进程内模型引擎测试（全部用假 Llama，不加载真实模型，离线、秒级）。

覆盖：<think> 拆分（含跨 chunk）、懒加载、串行锁、空闲卸载、统计、缺失文件报错、
LocalEmbedder、LocalProvider（complete / stream / configured / require_ready）、
build_providers 的 local 分派、warm_eager。
"""
import asyncio
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import local_models as lm  # noqa: E402


class FakeLlama:
    """最小 llama_cpp.Llama 替身：能按 stream=True/False 返回固定内容。"""

    instances: list["FakeLlama"] = []
    reply = "<think>先想一想</think>DNS 是电话簿。"
    stream_pieces = ["<thi", "nk>想</think>", "DNS ", "是电话簿。"]
    embed_dim = 8

    def __init__(self, **kw):
        self.kw = kw
        self.embedding = bool(kw.get("embedding"))
        FakeLlama.instances.append(self)

    def create_chat_completion(self, messages=None, max_tokens=None, temperature=None,
                               stream=False, **kw):
        if stream:
            return iter([{"choices": [{"delta": {"content": p}}]} for p in self.stream_pieces])
        return {"choices": [{"message": {"content": FakeLlama.reply},
                             "finish_reason": "stop"}],
                "usage": {"completion_tokens": 7}}

    def embed(self, text):
        return [0.5] * FakeLlama.embed_dim

    def tokenize(self, text, **kw):
        # 1 字符 = 1 token（够用来做裁剪断言）
        if isinstance(text, (bytes, bytearray)):
            text = bytes(text).decode("utf-8", "ignore")
        return [ord(c) for c in str(text)]

    def detokenize(self, tokens, **kw):
        return "".join(chr(t) for t in tokens).encode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        import tempfile

        lm.set_llama_class(FakeLlama)
        FakeLlama.instances = []
        FakeLlama.stream_pieces = ["<thi", "nk>想</think>", "DNS ", "是电话簿。"]
        # 引擎会检查文件存在；用空文件冒充（假 Llama 不读内容）
        self._tmp = Path(tempfile.mkdtemp(prefix="lm_test_"))
        self.gguf = self._tmp / "fake.gguf"
        self.gguf.write_bytes(b"GGUF")

    def tearDown(self):
        import shutil

        lm.set_llama_class(None)
        shutil.rmtree(self._tmp, ignore_errors=True)


class ThinkTests(unittest.TestCase):
    def test_paired(self):
        ans, think = lm.split_think("<think>a</think>正文")
        self.assertEqual(ans, "正文")
        self.assertIn("a", think)

    def test_multiple(self):
        ans, _ = lm.split_think("<think>1</think>A<think>2</think>B")
        self.assertEqual(ans, "AB")

    def test_unterminated(self):
        ans, think = lm.split_think("正文<think>没闭合")
        self.assertEqual(ans, "正文")
        self.assertIn("没闭合", think)

    def test_none(self):
        ans, think = lm.split_think("纯正文")
        self.assertEqual(ans, "纯正文")
        self.assertEqual(think, "")

    def test_strip(self):
        self.assertEqual(lm.strip_think("<think>x</think>ok"), "ok")


class EngineTests(Base):
    def test_lazy_load(self):
        eng = lm.LocalChatEngine(str(self.gguf))
        self.assertFalse(eng.loaded)
        self.assertEqual(FakeLlama.instances, [], "构造不该加载模型")
        res = eng.chat([{"role": "user", "content": "hi"}])
        self.assertTrue(eng.loaded)
        self.assertEqual(len(FakeLlama.instances), 1)
        self.assertEqual(res["content"], "DNS 是电话簿。")
        self.assertIn("先想一想", res["think"])

    def test_reuses_single_instance(self):
        eng = lm.LocalChatEngine(str(self.gguf))
        eng.chat([{"role": "user", "content": "a"}])
        eng.chat([{"role": "user", "content": "b"}])
        self.assertEqual(len(FakeLlama.instances), 1, "第二次不该重新加载")
        self.assertEqual(eng.calls, 2)
        self.assertEqual(eng.tokens, 14)

    def test_missing_file(self):
        eng = lm.LocalChatEngine(str(ROOT / "definitely-missing.gguf"))
        with self.assertRaises(lm.LocalModelError) as ctx:
            eng.chat([{"role": "user", "content": "x"}])
        self.assertIn("不存在", str(ctx.exception))

    def test_stream_filters_think_across_chunks(self):
        eng = lm.LocalChatEngine(str(self.gguf))
        out = "".join(eng.stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(out, "DNS 是电话簿。", "思考段不能被吐出来，标签被切开也不能漏")

    def test_stream_no_partial_tag_leak(self):
        FakeLlama.stream_pieces = ["<th", "in", "k>", "思考", "</think>", "正文"]
        eng = lm.LocalChatEngine(str(self.gguf))
        out = "".join(eng.stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(out, "正文")
        self.assertNotIn("<", out)

    def test_idle_unload(self):
        eng = lm.LocalChatEngine(str(self.gguf), idle_unload_seconds=0.01)
        eng.chat([{"role": "user", "content": "a"}])
        self.assertTrue(eng.loaded)
        time.sleep(0.05)
        eng.chat([{"role": "user", "content": "b"}])
        self.assertEqual(eng.loads, 2, "空闲超时后应重新加载")

    def test_stats(self):
        eng = lm.LocalChatEngine(str(self.gguf), n_ctx=2048, n_threads=4)
        eng.chat([{"role": "user", "content": "a"}])
        st = eng.stats()
        self.assertTrue(st["loaded"])
        self.assertEqual(st["n_ctx"], 2048)
        self.assertEqual(st["n_threads"], 4)
        self.assertEqual(st["calls"], 1)

    def test_warm_reports(self):
        eng = lm.LocalChatEngine(str(self.gguf))
        res = eng.warm()
        self.assertTrue(res["ok"])
        self.assertTrue(eng.loaded)


class EmbedderTests(Base):
    def test_embed(self):
        emb = lm.LocalEmbedder(str(self.gguf))
        vecs = emb.embed(["a", "b"])
        self.assertEqual(len(vecs), 2)
        self.assertEqual(emb.dim, FakeLlama.embed_dim)
        self.assertTrue(emb.name.startswith("local:"))
        self.assertTrue(FakeLlama.instances[0].embedding)


class ProviderTests(Base):
    def _provider(self, **cfg):
        from gantry.providers import build_providers

        path = ROOT / "gantry" / "local_models.py"
        c = {"backend": "local", "models": ["fake"], "model_path": str(path)}
        c.update(cfg)
        return build_providers({"providers": {"local": c}})["local"]

    def test_build_dispatches_to_local(self):
        from gantry.providers import LocalProvider

        prov = self._provider()
        self.assertIsInstance(prov, LocalProvider)
        self.assertEqual(prov.models, ["fake"])
        self.assertTrue(prov.configured)

    def test_missing_path_not_configured(self):
        prov = self._provider(model_path=str(ROOT / "nope.gguf"))
        self.assertFalse(prov.configured)
        with self.assertRaises(Exception):
            prov.require_ready()

    def test_complete_message(self):
        prov = self._provider()
        msg = asyncio.new_event_loop().run_until_complete(
            prov.complete_message("fake", [{"role": "user", "content": "hi"}]))
        self.assertEqual(msg["content"], "DNS 是电话簿。")
        self.assertIsNone(msg["tool_calls"])
        self.assertEqual(msg["finish_reason"], "stop")

    def test_complete_text(self):
        prov = self._provider()
        text = asyncio.new_event_loop().run_until_complete(
            prov.complete("fake", [{"role": "user", "content": "hi"}]))
        self.assertEqual(text, "DNS 是电话簿。")

    def test_stream_chunks(self):
        prov = self._provider()

        async def collect():
            out = []
            async for ch in prov.stream_chunks("fake", [{"role": "user", "content": "hi"}]):
                out.append(ch)
            return out

        chunks = asyncio.new_event_loop().run_until_complete(collect())
        text = "".join((c.get("delta") or {}).get("content") or "" for c in chunks)
        self.assertEqual(text, "DNS 是电话簿。")
        self.assertEqual(chunks[-1]["finish_reason"], "stop")


BASE_GGUF: list[str] = []


class WarmTests(Base):
    def test_warm_eager_only_for_flagged(self):
        class P:
            def __init__(self, eager):
                self.eager = eager
                self.engine = lm.LocalChatEngine(BASE_GGUF[0])

        BASE_GGUF[:] = [str(self.gguf)]
        a, b = P(True), P(False)
        lm.warm_eager({"a": a, "b": b})
        for _ in range(50):
            if a.engine.loaded:
                break
            time.sleep(0.01)
        self.assertTrue(a.engine.loaded, "eager 的应该被预热")
        self.assertFalse(b.engine.loaded, "非 eager 不该加载")


class KbLocalEmbedderTests(unittest.TestCase):
    def test_backend_local_selects_local_embedder(self):
        from gantry.kb_semantic import build_embedder

        emb = build_embedder({"backend": "local", "model_path": "x.gguf", "dim": 1024})
        self.assertIsInstance(emb, lm.LocalEmbedder)
        self.assertEqual(emb.dim, 1024)


class ContextFitTests(Base):
    """超窗口时必须自己裁剪，不能把 llama.cpp 的 'exceed context window' 抛给用户。"""

    def _engine(self, n_ctx=512, max_tokens=64, truncate=True):
        return lm.LocalChatEngine(str(self.gguf), n_ctx=n_ctx, max_tokens=max_tokens,
                                  truncate=truncate)

    def test_short_prompt_untouched(self):
        eng = self._engine()
        llm = eng._ensure()
        msgs = [{"role": "user", "content": "短"}]
        fitted, info = eng._fit(llm, msgs)
        self.assertEqual(fitted, msgs)
        self.assertEqual(info["dropped"], 0)
        self.assertFalse(info["truncated"])

    def test_drops_oldest_keeps_system_and_tail(self):
        eng = self._engine(n_ctx=400, max_tokens=32)   # 预算约 368
        llm = eng._ensure()
        msgs = [{"role": "system", "content": "SYS"},
                {"role": "user", "content": "旧" * 400},
                {"role": "assistant", "content": "旧答"},
                {"role": "user", "content": "新问题"}]
        fitted, info = eng._fit(llm, msgs)
        self.assertGreaterEqual(info["dropped"], 1)
        self.assertEqual(fitted[0]["content"], "SYS", "system 必须保留")
        self.assertEqual(fitted[-1]["content"], "新问题", "最近消息必须保留")
        self.assertLessEqual(info["tokens"], info["budget"])

    def test_single_huge_message_truncated(self):
        eng = self._engine(n_ctx=300, max_tokens=32)
        llm = eng._ensure()
        msgs = [{"role": "user", "content": "长" * 2000}]
        fitted, info = eng._fit(llm, msgs)
        self.assertTrue(info["truncated"])
        self.assertLessEqual(info["tokens"], info["budget"])
        self.assertLess(len(fitted[0]["content"]), 2000)

    def test_strict_mode_raises_actionable(self):
        eng = self._engine(n_ctx=300, max_tokens=32, truncate=False)
        llm = eng._ensure()
        msgs = [{"role": "user", "content": "长" * 2000}]
        with self.assertRaises(lm.LocalModelError) as ctx:
            eng._fit(llm, msgs)
        msg = str(ctx.exception)
        self.assertIn("n_ctx", msg)
        self.assertIn("165 MB", msg, "要给出内存代价")

    def test_chat_reports_trimmed(self):
        eng = self._engine(n_ctx=400, max_tokens=32)
        res = eng.chat([{"role": "system", "content": "S"},
                        {"role": "user", "content": "x" * 900}])
        self.assertIn("trimmed", res)
        self.assertLessEqual(res["trimmed"]["tokens"], res["trimmed"]["budget"])

    def test_budget_uses_max_prompt_tokens(self):
        eng = lm.LocalChatEngine(str(self.gguf), n_ctx=4096, max_prompt_tokens=100)
        self.assertEqual(eng._budget(), 100)


if __name__ == "__main__":
    unittest.main()
