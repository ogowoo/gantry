"""API 提供商接入层。

把 OpenAI 兼容的 HTTP API（OpenAI / DeepSeek / Kimi / Grok / xAI 等）接入桥接，
与 Copilot 网页自动化并列作为模型来源：

- 模型 ID 约定 "provider/model"，如 deepseek/deepseek-chat、kimi/kimi-k2-0905-preview；
- /v1/chat/completions 直接透传客户端 messages；
- Agent / 普通对话模式复用本地 Session 的消息记录作为上下文；
- API Key 走环境变量（推荐，不进 git）或 config.json 的 providers 段。

config.json 的 providers 段可覆盖预设或新增任意 OpenAI 兼容提供商：
    "providers": {
      "deepseek": {"models": ["deepseek-chat"]},
      "myapi": {"base_url": "https://example.com/v1", "api_key_env": "MYAPI_KEY",
                "models": ["my-model"], "description": "自建网关"}
    }
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, AsyncIterator

import httpx


def system_proxy() -> str | None:
    """读取本机系统代理（Windows 注册表 / 环境变量）。

    为什么需要它：浏览器走的是 Windows 系统代理（WinINET），而 Python 的 httpx
    默认只读 HTTP_PROXY/HTTPS_PROXY 环境变量、**不看系统代理**。于是出现
    "浏览器能打开 API 站点、本服务却报提供商不可用"这种最容易被误判成"对方挂了"
    的情况。urllib 在 Windows 上会读注册表，正好补上这个差异。
    """
    try:
        proxies = urllib.request.getproxies() or {}
    except Exception:
        return None
    for key in ("https", "http"):
        value = str(proxies.get(key) or "").strip()
        if value:
            return value
    return None

# 结构化输出能力：openai/grok 支持真·json_schema；deepseek/kimi 支持 json_object；
# 未列出的走 auto（先试 json_object，服务不支持时自动去掉并重试一次）。
JSON_MODE_BY_PROVIDER = {
    "openai": "json_schema",
    "deepseek": "json_object",
    "kimi": "json_object",
    "moonshot": "json_object",
    "grok": "json_schema",
    "xai": "json_schema",
}

PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "description": "OpenAI API",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "description": "DeepSeek API",
    },
    # Kimi Coding 订阅（Anthropic Messages 协议，非 OpenAI 兼容）。
    # 实测：用户的 sk-kim… key 在 api.kimi.com/coding 可用，在 moonshot 上 401。
    "kimi-coding": {
        "base_url": "https://api.kimi.com/coding",
        "api": "anthropic",
        "api_key_env": ["KIMI_API_KEY", "MONSHOT_API_KEY", "MOONSHOT_API_KEY"],
        "models": ["k3", "k3-256k", "kimi-for-coding", "kimi-for-coding-highspeed"],
        "max_tokens": 8192,
        "json_mode": "off",          # Anthropic 无 response_format，靠提示词约束
        "description": "Kimi Coding（Anthropic Messages 协议）",
    },
    "kimi": {
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": ["MOONSHOT_API_KEY", "KIMI_API_KEY", "MONSHOT_API_KEY"],
        "models": ["kimi-k2-0905-preview", "moonshot-v1-128k"],
        "description": "Kimi（Moonshot）API",
    },
    "grok": {
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "models": ["grok-4", "grok-3"],
        "description": "Grok（xAI）API",
    },
}


class ProviderError(RuntimeError):
    """API 提供商调用失败（非限流类）。"""


def _credential_hint(body: str) -> str:
    """把反代/上游的"凭据无效"翻译成用户能执行的一句话。

    典型：DeepSeek 网页反代返回 `{"code":40003,"msg":"INVALID_TOKEN"}`（token 过期），
    或者 401/403。原样抛给用户只有一串 JSON，不知道该干什么。
    """
    t = str(body or "")
    low = t.lower()
    if not any(k in low for k in ("invalid_token", "40003", "unauthorized",
                                  "invalid api key", "invalid_api_key")):
        return ""
    return ("（反代/上游的凭据已失效：重新登录或重新导入 token；"
            "若只是临时用一下，可以在反代的管理面板里删掉这条账号走游客态）")


class ProviderUnavailable(RuntimeError):
    """限流/5xx/超时等暂时不可用，上层按可重试处理。"""


def split_provider_model(model_id: str) -> tuple[str, str] | None:
    """'deepseek/deepseek-chat' -> ('deepseek', 'deepseek-chat')；非提供商格式返回 None。"""
    if "/" not in (model_id or ""):
        return None
    provider, _, model = model_id.partition("/")
    return (provider, model) if provider and model else None


def _read_env(name: str) -> str:
    """读环境变量；Windows 下进程环境过期时回退读注册表的 User/Machine 级值。

    场景：服务由某个早先启动的宿主进程拉起时，子进程继承的是旧环境块，
    用户刚在系统设置里添加的 KEY 不可见；注册表回退让这类情况也能读到。
    """
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg
            for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                              (winreg.HKEY_LOCAL_MACHINE,
                               r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        value, _ = winreg.QueryValueEx(key, name)
                        if value:
                            # REG_EXPAND_SZ 可能含 %VAR%，strip 去首尾空白
                            return os.path.expandvars(str(value)).strip()
                except OSError:
                    continue
        except ImportError:
            pass
    return ""


class ApiProvider:
    def __init__(self, name: str, cfg: dict[str, Any]):
        self.name = name
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        # api_key_env 支持字符串或列表；按顺序回退（实测用户会把 Kimi 的 key
        # 设成 KIMI_API_KEY，而官方示例是 MOONSHOT_API_KEY——两种都认）
        env_spec = cfg.get("api_key_env", "")
        env_names = ([str(x) for x in env_spec if str(x).strip()]
                     if isinstance(env_spec, (list, tuple))
                     else ([str(env_spec)] if str(env_spec).strip() else []))
        key = str(cfg.get("api_key") or "").strip()
        for _en in env_names:
            if key:
                break
            key = _read_env(_en) or ""
        self.api_key = key
        self.api_key_env = env_names[0] if env_names else ""
        self.api_key_envs = env_names
        self.models = [str(m) for m in (cfg.get("models") or [])]
        self.description = str(cfg.get("description") or name)
        self.timeout = float(cfg.get("timeout_seconds", 300))
        # 结构化输出模式：auto（默认，先试 json_object）/ json_schema /
        # json_object / off。不支持的服务会在 4xx 时自动降级重试一次。
        self.json_mode = str(cfg.get("json_mode", "auto")).lower()
        # 协议类型：openai（默认，/chat/completions）或 anthropic（/v1/messages）
        self.api = str(cfg.get("api", "openai")).lower()
        # 0 = **不发 max_tokens**（让服务端用自己的默认值；有的网关不认这个字段）。
        # 注意不能用 `or 8192`：那样 0 会被当成"没配"，永远发 8192。
        raw_max = cfg.get("max_tokens", 8192)
        self.max_tokens = int(raw_max) if raw_max not in (None, "") else 8192
        # 代理：显式 proxy 优先；否则 use_system_proxy（默认开）时用系统代理。
        # 关掉可强制直连（代理本身故障时用）。
        self.proxy = str(cfg.get("proxy") or "").strip() or None
        self.use_system_proxy = bool(cfg.get("use_system_proxy", True))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def require_ready(self) -> None:
        if not self.base_url:
            raise ProviderError(f"提供商 {self.name} 缺少 base_url")
        if not self.api_key:
            raise ProviderError(
                f"提供商 {self.name} 未配置 API Key：请设置环境变量 {self.api_key_env or '(未指定)'} "
                f"或在 config.json providers.{self.name}.api_key 中填写"
            )

    def proxy_url(self) -> str | None:
        if self.proxy:
            return self.proxy
        if self.use_system_proxy:
            return system_proxy()
        return None

    def _client(self, use_proxy: bool = True):
        kwargs: dict[str, Any] = {}
        if use_proxy:
            proxy = self.proxy_url()
            if proxy:
                kwargs["proxy"] = proxy
        headers: dict[str, str] = {"Authorization": f"Bearer {self.api_key}"}
        if self.api == "anthropic":
            # Kimi Coding 实测 Bearer 与 x-api-key 都接受；带上协议版本头更稳
            headers["x-api-key"] = self.api_key
            headers["anthropic-version"] = "2023-06-01"
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(self.timeout, connect=30),
            **kwargs,
        )

    def net_error_text(self, exc: Exception | None) -> str:
        """传输层异常的可读描述。

        httpx 的 ReadTimeout/ConnectError 等 str() **常为空串**，只报类名等于什么都没
        说（用户实测：步骤里"服务暂时不可用（第 1 次）："后面是空的，看不出原因）。
        这里补上异常类名、目标地址、是否经代理，并给出可操作的排查提示。
        """
        name = type(exc).__name__ if exc is not None else "NetworkError"
        detail = str(exc).strip() if exc is not None else ""
        via = self.proxy_url()
        where = f"经代理 {via}" if via else "直连"
        return (f"{where} 访问 {self.base_url} 时网络异常（{name}）："
                f"{detail[:200] if detail else '对端无详细消息'}。"
                "若浏览器能打开该站点而这里失败，多半是代理差异——"
                "可在 providers 配置里设 proxy，或设 use_system_proxy=false 强制直连")

    @staticmethod
    @staticmethod
    async def _check_status_stream(resp: httpx.Response) -> None:
        """`client.stream()` 里的状态检查。

        流式响应还没被消费时访问 `resp.text` 会抛
        "Attempted to access streaming response content, without having called `read()`"
        ——实测把整个提供商通道的 Agent 回合打成 500。错误时先 `aread()` 再读 body。
        """
        if resp.status_code < 400:
            return
        try:
            await resp.aread()
            body = resp.text[:500]
        except Exception:
            body = "(无法读取响应体)"
        if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}: {body}{_credential_hint(body)}")
        raise ProviderError(f"HTTP {resp.status_code}: {body}{_credential_hint(body)}")

    @staticmethod
    def _check_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        body = resp.text[:500]
        if resp.status_code in (408, 409, 425, 429) or resp.status_code >= 500:
            raise ProviderUnavailable(f"HTTP {resp.status_code}: {body}{_credential_hint(body)}")
        raise ProviderError(f"HTTP {resp.status_code}: {body}{_credential_hint(body)}")

    def _json_hint(self, schema: dict[str, Any] | None) -> dict[str, Any]:
        """按能力生成 response_format；拿不准就退回 json_object（兼容最广）。"""
        if not schema or self.json_mode == "off":
            return {}
        if self.json_mode in ("json_schema", "schema"):
            return {"response_format": {"type": "json_schema", "json_schema": {
                "name": "agent_action", "strict": False, "schema": schema}}}
        return {"response_format": {"type": "json_object"}}

    @staticmethod
    def _anthropic_body(model: str, messages: list[dict[str, Any]],
                        max_tokens: int, stream: bool,
                        tools: list[dict[str, Any]] | None = None,
                        tool_choice: Any = None) -> dict[str, Any]:
        """OpenAI 风格的 messages → Anthropic Messages 请求体。"""
        system_parts = [m.get("content") or "" for m in messages if m.get("role") == "system"]
        conv = [{"role": m.get("role"), "content": m.get("content") or ""}
                for m in messages if m.get("role") in ("user", "assistant")]
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens,
                                "messages": conv or [{"role": "user", "content": ""}]}
        if system_parts:
            body["system"] = "\n\n".join(x for x in system_parts if x)
        if stream:
            body["stream"] = True
        if tools:
            body["tools"] = [
                {"name": (t.get("function") or {}).get("name") or t.get("name") or "",
                 "description": (t.get("function") or {}).get("description") or "",
                 "input_schema": (t.get("function") or {}).get("parameters")
                 or {"type": "object", "properties": {}}}
                for t in tools if isinstance(t, dict)
            ]
        if tool_choice is not None:
            if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
                name = (tool_choice.get("function") or {}).get("name")
                if name:
                    body["tool_choice"] = {"type": "tool", "name": name}
            elif tool_choice in ("auto", "any", "none"):
                body["tool_choice"] = {"type": tool_choice}
        return body

    @staticmethod
    def _anthropic_text(data: dict[str, Any]) -> str:
        blocks = data.get("content") or []
        return "".join(str(b.get("text") or "") for b in blocks if isinstance(b, dict)
                       and b.get("type") == "text")

    async def complete_message(self, model: str, messages: list[dict[str, Any]],
                               response_schema: dict[str, Any] | None = None,
                               *, tools: list[dict[str, Any]] | None = None,
                               tool_choice: Any = None) -> dict[str, Any]:
        """返回完整的 assistant message：content / tool_calls / finish_reason。

        `/v1/chat/completions` 用这个把提供商的 tool_calls 原样透传给 DSH 等客户端；
        内部 Agent 调用仍走 `complete()`（只要正文）。
        """
        self.require_ready()
        if self.api == "anthropic":
            body = self._anthropic_body(model, messages, self.max_tokens or 8192,
                                        stream=False, tools=tools, tool_choice=tool_choice)
            try:
                async with self._client() as client:
                    resp = await client.post("/v1/messages", json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ProviderUnavailable(self.net_error_text(exc)) from exc
            self._check_status(resp)
            try:
                data = resp.json()
            except Exception as exc:
                raise ProviderError(f"响应格式异常: {exc}") from exc
            text = self._anthropic_text(data)
            tool_calls: list[dict[str, Any]] = []
            for block in data.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": json.dumps(block.get("input") or {},
                                                    ensure_ascii=False),
                        },
                    })
            stop = str(data.get("stop_reason") or "")
            finish = ("tool_calls" if tool_calls else
                      "stop" if stop in ("", "end_turn", "stop_sequence") else stop)
            return {"content": text, "tool_calls": tool_calls or None,
                    "finish_reason": finish, "raw": data}
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        # max_tokens 以前只给 anthropic 用，OpenAI 协议这边**从没发出去**过：
        # 思考型模型（Qwen3.5 等）会把服务端默认预算全花在 reasoning 上，
        # content 直接为空（本地 Ollama 实测：默认预算下只回 reasoning）。
        if self.max_tokens:
            payload["max_tokens"] = int(self.max_tokens)
        payload.update(self._json_hint(response_schema))
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        # 先按配置（可能含自动探测到的系统代理）请求；传输层失败且代理是**自动探测**
        # 来的，再直连重试一次——避免"系统代理存在但连不上"把可用服务判死。
        attempts = [True]
        if self.proxy_url() and not self.proxy:
            attempts.append(False)
        last_exc: Exception | None = None
        for use_proxy in attempts:
            try:
                async with self._client(use_proxy=use_proxy) as client:
                    resp = await client.post("/chat/completions", json=payload)
                    if resp.status_code >= 400 and "response_format" in payload:
                        # 服务不支持该参数：去掉后重试一次（能力探测，不报错给用户）
                        payload.pop("response_format", None)
                        resp = await client.post("/chat/completions", json=payload)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                continue
            self._check_status(resp)
            try:
                choice = resp.json()["choices"][0]
                message = choice.get("message") or {}
            except Exception as exc:
                raise ProviderError(f"响应格式异常: {exc}") from exc
            content = message.get("content")
            if isinstance(content, list):
                content = "".join(
                    str(p.get("text") or "") if isinstance(p, dict) else str(p)
                    for p in content)
            content = str(content or "")
            tool_calls = message.get("tool_calls") or None
            if not content and not tool_calls:
                # 只拿到思考过程：把"为什么空 + 怎么改"直接写进错误，别再让上层猜
                think = str(message.get("reasoning") or message.get("reasoning_content") or "")
                if think:
                    raise ProviderError(
                        f"{self.name}/{model} 只返回了思考过程（reasoning），正文为空。"
                        f"多半是 max_tokens 太小（当前 {self.max_tokens}）被思考吃光了，"
                        f"或这是思考型模型。处理：把 config.json 里 "
                        f"providers.{self.name}.max_tokens 调大（例如 4096），"
                        f"或换一个非思考模型。")
                raise ProviderError(
                    f"{self.name}/{model} 返回空正文（响应里既没有 content 也没有 reasoning）")
            finish = choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
            return {"content": content, "tool_calls": tool_calls,
                    "finish_reason": finish, "raw": message}
        raise ProviderUnavailable(self.net_error_text(last_exc)) from last_exc

    async def complete(self, model: str, messages: list[dict[str, Any]],
                       response_schema: dict[str, Any] | None = None) -> str:
        msg = await self.complete_message(model, messages, response_schema)
        if msg.get("tool_calls") and not msg.get("content"):
            raise ProviderError(
                f"{self.name}/{model} 返回了工具调用（tool_calls），当前调用方不支持工具调用")
        return str(msg.get("content") or "")

    async def stream_chunks(self, model: str, messages: list[dict[str, Any]],
                            response_schema: dict[str, Any] | None = None,
                            *, tools: list[dict[str, Any]] | None = None,
                            tool_choice: Any = None
                            ) -> AsyncIterator[dict[str, Any]]:
        """流式产出 OpenAI 风格的 choice 增量（delta 里可能是 content 或 tool_calls）。

        `/v1/chat/completions` 用它把提供商的 tool_calls 分片原样转发给客户端；
        `stream()` 只是它的纯文本视图（内部 Agent 用）。
        """
        self.require_ready()
        if self.api == "anthropic":
            body = self._anthropic_body(model, messages, self.max_tokens or 8192,
                                        stream=True, tools=tools, tool_choice=tool_choice)
            tool_index = 0
            try:
                async with self._client() as client:
                    async with client.stream("POST", "/v1/messages", json=body) as resp:
                        await self._check_status_stream(resp)
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            raw = line[5:].strip()
                            try:
                                ev = json.loads(raw)
                            except Exception:
                                continue
                            kind = ev.get("type")
                            if kind == "content_block_start":
                                block = ev.get("content_block") or {}
                                if block.get("type") == "tool_use":
                                    yield {"delta": {"tool_calls": [{
                                        "index": tool_index,
                                        "id": str(block.get("id") or ""),
                                        "type": "function",
                                        "function": {"name": str(block.get("name") or ""),
                                                     "arguments": ""},
                                    }]}, "finish_reason": None}
                                    tool_index += 1
                            elif kind == "content_block_delta":
                                delta = ev.get("delta") or {}
                                if delta.get("type") == "text_delta" and delta.get("text"):
                                    yield {"delta": {"content": delta["text"]},
                                           "finish_reason": None}
                                elif delta.get("type") == "input_json_delta":
                                    yield {"delta": {"tool_calls": [{
                                        "index": max(0, tool_index - 1),
                                        "function": {"arguments": delta.get("partial_json") or ""},
                                    }]}, "finish_reason": None}
                            elif kind == "message_delta":
                                stop = (ev.get("delta") or {}).get("stop_reason")
                                if stop:
                                    yield {"delta": {},
                                           "finish_reason": "tool_calls"
                                           if stop == "tool_use" else "stop"}
                            elif kind == "message_stop":
                                return
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ProviderUnavailable(self.net_error_text(exc)) from exc
            return
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        body.update(self._json_hint(response_schema))
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        try:
            async with self._client() as client:
                async with client.stream("POST", "/chat/completions", json=body) as resp:
                    await self._check_status_stream(resp)
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        try:
                            chunk = json.loads(payload)
                        except Exception:
                            continue
                        for choice in chunk.get("choices") or []:
                            delta = choice.get("delta") or {}
                            finish = choice.get("finish_reason")
                            if delta or finish:
                                yield {"delta": delta, "finish_reason": finish}
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise ProviderUnavailable(self.net_error_text(exc)) from exc

    async def stream(self, model: str, messages: list[dict[str, Any]],
                     response_schema: dict[str, Any] | None = None) -> AsyncIterator[str]:
        """流式产出 content 增量（内部 Agent 用；工具分片请用 stream_chunks）。"""
        async for choice in self.stream_chunks(model, messages, response_schema):
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                yield delta


class LocalProvider(ApiProvider):
    """进程内 GGUF 提供商（`backend: "local"`）：模型跑在服务进程里，不走 HTTP。

    和 ApiProvider 同构（complete_message / stream_chunks / models），因此
    `/v1/models`、Agent、RAG、/v1/chat/completions 都不需要改。
    限制：暂不支持原生 tool_calls 与 `json_schema` 严格模式（用 json_object 提示）。
    """

    def __init__(self, name: str, cfg: dict[str, Any]):
        super().__init__(name, cfg)
        from .local_models import LocalChatEngine

        self.backend = "local"
        self.model_path = str(cfg.get("model_path") or "")
        self.eager = bool(cfg.get("eager", False))
        self.engine = LocalChatEngine(
            self.model_path,
            n_ctx=int(cfg.get("n_ctx") or 16384),
            n_threads=int(cfg.get("n_threads") or 0),
            max_tokens=int(cfg.get("max_tokens") or 2048),
            temperature=float(cfg.get("temperature", 0.7)),
            idle_unload_seconds=float(cfg.get("idle_unload_seconds") or 0),
            verbose=bool(cfg.get("verbose", False)),
            max_prompt_tokens=int(cfg.get("max_prompt_tokens") or 0),
            truncate=bool(cfg.get("truncate", True)))
        self.base_url = self.base_url or "local://in-process"
        self.api_key = self.api_key or "local"

    @property
    def configured(self) -> bool:
        return bool(self.model_path and Path(self.model_path).is_file())

    def require_ready(self) -> None:
        if not self.model_path:
            raise ProviderError(
                f"本地提供商 {self.name} 缺少 model_path（在 config.json 的 "
                f"providers.{self.name}.model_path 指定 GGUF 路径）")
        if not Path(self.model_path).is_file():
            raise ProviderError(f"本地提供商 {self.name} 的模型文件不存在：{self.model_path}")

    async def complete_message(self, model: str, messages: list[dict[str, Any]],
                               response_schema: dict[str, Any] | None = None,
                               *, tools: list[dict[str, Any]] | None = None,
                               tool_choice: Any = None) -> dict[str, Any]:
        import asyncio

        self.require_ready()
        if hasattr(self.engine, 'clear_cancel'):
            self.engine.clear_cancel()
        res = await asyncio.to_thread(
            self.engine.chat, messages, response_schema=response_schema)
        return {"content": res.get("content") or "",
                "tool_calls": None,
                "finish_reason": res.get("finish_reason") or "stop",
                "raw": res}

    async def stream_chunks(self, model: str, messages: list[dict[str, Any]],
                            response_schema: dict[str, Any] | None = None,
                            *, tools: list[dict[str, Any]] | None = None,
                            tool_choice: Any = None):
        import asyncio

        self.require_ready()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def worker() -> None:
            try:
                for piece in self.engine.stream(messages, response_schema=response_schema):
                    asyncio.run_coroutine_threadsafe(queue.put(("delta", piece)), loop)
            except Exception as exc:             # noqa: BLE001
                asyncio.run_coroutine_threadsafe(queue.put(("error", exc)), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(("done", None)), loop)

        import threading

        t = threading.Thread(target=worker, name=f"local-{self.name}", daemon=True)
        t.start()
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "delta":
                    yield {"delta": {"content": payload}, "finish_reason": None}
                elif kind == "error":
                    raise ProviderUnavailable(str(payload)[:300]) from payload
                else:
                    yield {"delta": {}, "finish_reason": "stop"}
                    return
        finally:
            # 协作式取消：消费者（provider_turn）被取消了，通知 engine 尽快退出；
            # 不能直接 join 线程（它可能还在 llama.cpp 里），但 cancel 信号
            # 会让 stream() 在下一个 chunk 检查时 break。
            if hasattr(self.engine, 'cancel'):
                self.engine.cancel()

    async def complete(self, model: str, messages: list[dict[str, Any]],
                       response_schema: dict[str, Any] | None = None) -> str:
        msg = await self.complete_message(model, messages, response_schema)
        return str(msg.get("content") or "")


def _autodiscover_local(merged: dict[str, dict[str, Any]]) -> None:
    """data/models/ 里有 GGUF 且没人配过本地提供商时，自动注册 `local/` 提供商。

    约定：文件名含 embed / bge / e5 / mmproj 的视为向量或辅助模型，不做聊天模型。
    config.json 里显式配了 backend=local 或 model_path 时以配置为准，不重复注册。
    """
    for c in merged.values():
        if not isinstance(c, dict):
            continue
        if str(c.get("backend") or "").lower() == "local" or c.get("model_path"):
            return
    import importlib.util

    if importlib.util.find_spec("llama_cpp") is None:
        return  # 没装 llama-cpp-python：注册了也只会报"缺依赖"，不如不列
    root = Path(__file__).resolve().parent.parent
    mdir = root / "data" / "models"
    if not mdir.is_dir():
        return

    def _is_embed(name: str) -> bool:
        n = name.lower()
        return any(k in n for k in ("embed", "bge-", "bge_", "e5-", "mmproj"))

    chat = sorted(p for p in mdir.glob("*.gguf") if p.is_file() and not _is_embed(p.name))
    if not chat:
        return
    pick = chat[0]                      # 多个聊天 GGUF 时取排序第一个，其余仍走显式配置
    model_id = pick.stem.lower().replace(" ", "-")
    merged["local"] = {
        "backend": "local",
        "models": [model_id],
        "model_path": str(pick),
        "description": "进程内 GGUF（data/models 自动发现）",
    }


def build_providers(cfg: dict[str, Any]) -> dict[str, ApiProvider]:
    """内置预设与 config.json 的 providers 段合并（后者覆盖/新增）。"""
    merged: dict[str, dict[str, Any]] = {name: dict(preset) for name, preset in PRESETS.items()}
    for name, custom in (cfg.get("providers") or {}).items():
        if isinstance(custom, dict):
            merged.setdefault(name, {}).update(custom)
    _autodiscover_local(merged)
    out: dict[str, ApiProvider] = {}
    for name, c in merged.items():
        cfg2 = dict(c)
        # 未显式配置时按已知能力给默认值（不支持的会在 4xx 时自动降级重试）
        cfg2.setdefault("json_mode", JSON_MODE_BY_PROVIDER.get(name.lower(), "auto"))
        # backend=local（或给了 model_path）→ 进程内引擎，不走 HTTP
        if str(cfg2.get("backend") or "").lower() == "local" or cfg2.get("model_path"):
            out[name] = LocalProvider(name, cfg2)
        else:
            out[name] = ApiProvider(name, cfg2)
    return out
