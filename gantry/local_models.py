"""内嵌本地模型引擎：把 GGUF 直接跑在 Gantry 进程里，`python start.py` 就能用，不需要外部 server。

## 为什么要它

现在本地模型是外挂 `llama-server.exe`（18083 聊天 / 18085 embedding）——多两个进程、多一份
启动脚本、端口还得记。本模块用 `llama_cpp`（Python 绑定，已装）把模型加载进服务进程：

- **懒加载**：`eager: false`（默认）时第一次真正用到才加载，`start.py` 秒起；
  `eager: true` 在后台线程预热，启动不阻塞。
- **单飞锁**：llama.cpp 上下文不是线程安全的，同一引擎的生成串行执行（队列天然公平）。
- **空闲卸载**：`idle_unload_seconds > 0` 时，超过这个秒数没用就释放（省内存），下次再懒加载。
- **推理模型兼容**：MiniCPM5/Qwen3.5 这类会把 `<think>…</think>` 写进 content，`split_think()`
  把它拆出来，正文照常返回（否则答案里混着思考过程）。

## 用法

    eng = LocalChatEngine(path="E:/models/gguf/MiniCPM5-2B-Q4_K_M.gguf",
                          n_ctx=4096, n_threads=12)
    text = eng.chat([{"role": "user", "content": "你好"}])
    for delta in eng.stream([...]): ...

    emb = LocalEmbedder(path="E:/models/gguf/Qwen3-Embedding-0.6B-Q8_0.gguf")
    vecs = emb.embed(["VPN 连不上怎么办"])

`set_llama_class()` 供测试注入假实现，避免测试真的加载模型。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator

_THINK_RE = re.compile(r"(?is)<think\b[^>]*>.*?</think\s*>")
_THINK_OPEN = re.compile(r"(?is)<think\b[^>]*>")

_LLAMA: Any = None


def llama_class():
    """延迟导入 llama_cpp.Llama（没装时给出可行动的错误，而不是 ImportError 栈）。"""
    global _LLAMA
    if _LLAMA is None:
        try:
            from llama_cpp import Llama  # noqa: PLC0415
        except Exception as exc:                 # noqa: BLE001
            raise RuntimeError(
                "进程内模型需要 llama-cpp-python：pip install llama-cpp-python"
                f"（当前不可用：{exc}）") from exc
        _LLAMA = Llama
    return _LLAMA


def set_llama_class(cls: Any) -> None:
    """测试用：注入假的 Llama 类（传 None 恢复真实实现）。"""
    global _LLAMA
    _LLAMA = cls


def split_think(text: str) -> tuple[str, str]:
    """把 `<think>…</think>` 从输出里拆出来：返回 (正文, 思考内容)。

    推理模型（MiniCPM5、Qwen3.5 等）会把思考过程塞进 content。只剥**成对**标签；
    出现未闭合的 `<think>` 时，把开标签之后的内容整体当作思考（宁可少给也不混进正文）。
    """
    raw = str(text or "")
    think_parts = [m.group(0) for m in _THINK_RE.finditer(raw)]
    answer = _THINK_RE.sub("", raw)
    open_match = _THINK_OPEN.search(answer)
    if open_match:                               # 未闭合：保守处理
        think_parts.append(answer[open_match.start():])
        answer = answer[:open_match.start()]
    think = "\n".join(p.strip() for p in think_parts).strip()
    return answer.strip(), think


def strip_think(text: str) -> str:
    return split_think(text)[0]


def _tag_prefix(buf: str, tag: str) -> str:
    """buf 末尾若是 tag 的部分前缀，返回这段（要扣住）；否则返回空串。"""
    for i in range(len(buf)):
        tail = buf[i:]
        if tag.startswith(tail):
            return tail
    return ""


def _split_tag_prefix(buf: str, tag: str) -> tuple[str, str]:
    """把 buf 拆成（可以安全吐出的部分, 需要扣住的尾巴）。"""
    idx = buf.rfind("<")
    if idx >= 0:
        tail = buf[idx:]
        if tag.startswith(tail):
            return buf[:idx], tail
    return buf, ""


class LocalModelError(RuntimeError):
    """本地模型不可用/加载失败（与 ProviderError 同层级，调用方按"服务不可用"处理）。"""


class LocalChatEngine:
    """一个 GGUF 的聊天引擎（懒加载 + 串行生成 + 空闲卸载）。"""

    def __init__(self, path: str, *, n_ctx: int = 4096, n_threads: int = 0,
                 temperature: float = 0.7, max_tokens: int = 2048,
                 idle_unload_seconds: float = 0.0, chat_format: str | None = None,
                 verbose: bool = False):
        self.path = str(path or "")
        self.n_ctx = max(256, int(n_ctx or 4096))
        self.n_threads = int(n_threads or 0)
        self.temperature = float(temperature)
        self.max_tokens = max(1, int(max_tokens or 2048))
        self.idle_unload_seconds = float(idle_unload_seconds or 0)
        self.chat_format = chat_format or None
        self.verbose = bool(verbose)
        self._llm: Any = None
        self._lock = threading.Lock()
        self._last_used = 0.0
        self.load_seconds = 0.0
        self.loads = 0
        self.calls = 0
        self.tokens = 0
        self.seconds = 0.0

    # ── 生命周期 ──
    @property
    def loaded(self) -> bool:
        return self._llm is not None

    def _maybe_idle_unload(self) -> None:
        if (self._llm is not None and self.idle_unload_seconds > 0
                and time.time() - self._last_used > self.idle_unload_seconds):
            self.unload()

    def _ensure(self) -> Any:
        self._maybe_idle_unload()
        if self._llm is not None:
            return self._llm
        if not self.path or not Path(self.path).is_file():
            raise LocalModelError(f"模型文件不存在：{self.path or '(未配置)'}")
        cls = llama_class()
        t0 = time.time()
        try:
            kw: dict[str, Any] = {"model_path": self.path, "n_ctx": self.n_ctx,
                                  "verbose": self.verbose}
            if self.n_threads:
                kw["n_threads"] = self.n_threads
            if self.chat_format:
                kw["chat_format"] = self.chat_format
            self._llm = cls(**kw)
        except Exception as exc:                 # noqa: BLE001
            raise LocalModelError(f"加载 {Path(self.path).name} 失败：{str(exc)[:200]}") from exc
        self.load_seconds = round(time.time() - t0, 2)
        self.loads += 1
        return self._llm

    def warm(self) -> dict[str, Any]:
        """显式预热（eager 用）；失败不抛，返回状态供日志。"""
        try:
            self._ensure()
            return {"ok": True, **self.stats()}
        except Exception as exc:                 # noqa: BLE001
            return {"ok": False, "error": str(exc)[:200], **self.stats()}

    def unload(self) -> None:
        with self._lock:
            llm, self._llm = self._llm, None
        del llm

    def stats(self) -> dict[str, Any]:
        return {"path": self.path, "loaded": self.loaded, "loads": self.loads,
                "load_seconds": self.load_seconds, "calls": self.calls,
                "tokens": self.tokens,
                "tok_per_second": round(self.tokens / self.seconds, 1) if self.seconds else 0.0,
                "n_ctx": self.n_ctx, "n_threads": self.n_threads}

    # ── 生成 ──
    def _payload(self, messages: list[dict[str, Any]], max_tokens: int | None,
                 temperature: float | None, response_schema: dict[str, Any] | None,
                 stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": self.temperature if temperature is None else float(temperature),
            "stream": stream,
        }
        if response_schema:
            body["response_format"] = {"type": "json_object"}
        return body

    def chat(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
             temperature: float | None = None,
             response_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """同步生成：返回 {content, think, finish_reason, usage}（content 已剥掉 <think>）。"""
        llm = self._ensure()
        body = self._payload(messages, max_tokens, temperature, response_schema, False)
        with self._lock:                         # llama.cpp 上下文不能并发
            t0 = time.time()
            try:
                out = llm.create_chat_completion(**body)
            except Exception as exc:             # noqa: BLE001
                raise LocalModelError(f"生成失败：{str(exc)[:200]}") from exc
            el = time.time() - t0
            self._last_used = time.time()
            self.calls += 1
            self.seconds += el
        usage = out.get("usage") or {}
        used = int(usage.get("completion_tokens") or 0)
        self.tokens += used
        msg = ((out.get("choices") or [{}])[0].get("message") or {})
        raw = msg.get("content") or ""
        think_field = str(msg.get("reasoning_content") or "")
        answer, think = split_think(raw)
        if not answer and think_field:
            answer = ""                          # 只有思考、没有正文，交给上层判断
        return {"content": answer, "think": think or think_field,
                "finish_reason": (out.get("choices") or [{}])[0].get("finish_reason"),
                "usage": usage, "seconds": round(el, 3), "model": Path(self.path).stem}

    def stream(self, messages: list[dict[str, Any]], *, max_tokens: int | None = None,
               temperature: float | None = None,
               response_schema: dict[str, Any] | None = None) -> Iterator[str]:
        """流式产出**正文**增量（`<think>` 段被过滤，不吐给用户）。"""
        llm = self._ensure()
        body = self._payload(messages, max_tokens, temperature, response_schema, True)
        with self._lock:
            t0 = time.time()
            try:
                stream = llm.create_chat_completion(**body)
                in_think = False
                buf = ""
                for chunk in stream:
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                    piece = str(delta.get("content") or "")
                    if not piece:
                        continue
                    buf += piece
                    while buf:
                        if in_think:
                            end = buf.find("</think")
                            if end < 0:
                                # 尾部可能是 "</think" 的前缀（如 "</thi"）→ 扣住
                                keep = _tag_prefix(buf, "</think")
                                buf = keep
                                break
                            close = buf.find(">", end)
                            buf = buf[close + 1:] if close >= 0 else ""
                            in_think = False
                            continue
                        start = buf.find("<think")
                        if start < 0:
                            out, buf = _split_tag_prefix(buf, "<think")
                            if out:
                                yield out
                            break
                        if start > 0:
                            yield buf[:start]
                        buf = buf[start:]
                        close = buf.find(">")
                        if close < 0:
                            break
                        buf = buf[close + 1:]
                        in_think = True
                if buf and not in_think:
                    yield buf
            except Exception as exc:             # noqa: BLE001
                raise LocalModelError(f"流式生成失败：{str(exc)[:200]}") from exc
            finally:
                el = time.time() - t0
                self._last_used = time.time()
                self.calls += 1
                self.seconds += el


class LocalEmbedder:
    """进程内 embedding（实现 kb_semantic.Embedder 协议：name / dim / embed）。"""

    def __init__(self, path: str, *, n_ctx: int = 2048, n_threads: int = 0,
                 dim: int = 0, verbose: bool = False):
        self.path = str(path or "")
        self.n_ctx = max(128, int(n_ctx or 2048))
        self.n_threads = int(n_threads or 0)
        self.dim = int(dim or 0)
        self.verbose = bool(verbose)
        self.name = f"local:{Path(self.path).stem}" if self.path else "local:?"
        self._llm: Any = None
        self._lock = threading.Lock()

    def _ensure(self) -> Any:
        if self._llm is not None:
            return self._llm
        if not self.path or not Path(self.path).is_file():
            raise LocalModelError(f"embedding 模型不存在：{self.path or '(未配置)'}")
        cls = llama_class()
        kw: dict[str, Any] = {"model_path": self.path, "n_ctx": self.n_ctx,
                              "embedding": True, "verbose": self.verbose}
        if self.n_threads:
            kw["n_threads"] = self.n_threads
        self._llm = cls(**kw)
        return self._llm

    def embed(self, texts: list[str]) -> list[list[float]]:
        llm = self._ensure()
        out: list[list[float]] = []
        with self._lock:
            for text in texts:
                vec = llm.embed(str(text or " "))
                out.append([float(x) for x in vec])
        if out and not self.dim:
            self.dim = len(out[0])
        return out


def warm_eager(providers: dict[str, Any]) -> list[dict[str, Any]]:
    """后台预热所有 `eager` 的本地提供商；返回每个的加载结果（供日志）。

    只 warm 不加载模型文件以外的资源；异常一律吞掉（预热失败不该影响服务启动）。
    """
    import threading

    results: list[dict[str, Any]] = []

    def run(name: str, engine: Any) -> None:
        try:
            res = engine.warm()
        except Exception as exc:                 # noqa: BLE001
            res = {"ok": False, "error": str(exc)[:200]}
        res["provider"] = name
        results.append(res)

    threads = []
    for name, prov in (providers or {}).items():
        engine = getattr(prov, "engine", None)
        if engine is None or not getattr(prov, "eager", False):
            continue
        th = threading.Thread(target=run, args=(name, engine),
                              name=f"warm-{name}", daemon=True)
        th.start()
        threads.append(th)
    return results  # 结果由线程回填；调用方可稍后再看（或看引擎 stats()）
