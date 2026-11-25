"""第三方模型提供商的 OAuth 授权（设备码 / localhost 回跳）。

为什么单独一个模块：各厂商端点、client_id、刷新语义都不同，集中成一张
注册表（OAUTH_PROVIDERS）+ 两个通用流程引擎，加新厂商基本只填表。

端点与 client_id 来源（均为各厂商官方开源客户端的公开注册值）：
- kimi-coding：MoonshotAI/kimi-cli src/kimi_cli/auth/oauth.py（RFC 8628 设备码）
- openai-codex：openai/codex codex-rs/login/src/server.rs（授权码 + 本地回跳，
  回跳地址只允许 localhost:1455/1457，是 OpenAI 应用注册的硬性白名单）
- qwen：QwenLM/qwen-code packages/core/src/qwen/qwenOAuth2.ts（设备码 + PKCE）

安全约定：
- token 只写 data/oauth_tokens.json（data/ 在 .gitignore），绝不进 config.json；
- 授权 URL 带 state 防 CSRF、PKCE 防授权码被截获；
- 短寿命 token（Kimi 15 分钟）临期自动用 refresh_token 换新，带锁防并发刷新；
- 日志只记状态与错误类别，绝不记 token 本体。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

log = logging.getLogger(__name__)

# ── 厂商注册表 ────────────────────────────────────────────────
# flow=device：RFC 8628 设备码（无需本地端口，浏览器/无头环境都能用）
# flow=callback：授权码 + 127.0.0.1 临时回跳（端口由厂商应用注册钉死）
OAUTH_PROVIDERS: dict[str, dict[str, Any]] = {
    "kimi-coding": {
        "flow": "device",
        "label": "Kimi Code 订阅",
        "device_url": "https://auth.kimi.com/api/oauth/device_authorization",
        "token_url": "https://auth.kimi.com/api/oauth/token",
        "client_id": "17e5f671-d194-4dfb-9706-5516cb48c098",
        # kimi-cli 要求的平台标识头；缺失会被 400
        "extra_headers": {"X-Msh-Platform": "kimi_code_cli"},
    },
    "openai-codex": {
        "flow": "callback",
        "label": "OpenAI Codex（ChatGPT 账号）",
        "authorize_url": "https://auth.openai.com/oauth/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        # OpenAI 应用注册只允许这两个回跳端口，换端口会被 authorize 拒绝
        "ports": [1455, 1457],
        "redirect_path": "/auth/callback",
        "scope": "openid profile email offline_access "
                 "api.connectors.read api.connectors.invoke",
        "extra_params": {"id_token_add_organizations": "true",
                         "codex_cli_simplified_flow": "true",
                         "originator": "gantry"},
    },
    "qwen": {
        "flow": "device",
        "label": "Qwen（通义灵码订阅）",
        "device_url": "https://chat.qwen.ai/api/oauth/device/code",
        "token_url": "https://chat.qwen.ai/api/oauth/token",
        "client_id": "f0304373b74a44d2b584a3fb8b0a533",
        # qwen-code 在轮询 token 时附带 PKCE verifier
        "pkce_in_poll": True,
        "resource": "https://portal.qwen.ai/v1",
    },
}

_TIMEOUT = 30
_REFRESH_MARGIN = 120          # 距过期不足 120 秒就提前刷新
_FLOWS: dict[str, dict[str, Any]] = {}   # flow_id → 流程状态（内存态，重启即失效）
_FLOWS_LOCK = threading.Lock()
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()

_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


# ── token 存储 ────────────────────────────────────────────────
def _store_path(root: Path) -> Path:
    return Path(root) / "data" / "oauth_tokens.json"


def load_store(root: Path) -> dict[str, Any]:
    p = _store_path(root)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:                        # noqa: BLE001
        return {}


def save_store(root: Path, store: dict[str, Any]) -> None:
    p = _store_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)                       # 原子替换，避免写一半损坏
    try:
        os.chmod(p, 0o600)                   # 尽力而为；Windows 上无实际效果
    except OSError:
        pass


def _save_tokens(root: Path, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """把 token 端点响应规整成统一结构落盘。"""
    rec = {
        "access_token": str(payload.get("access_token") or ""),
        "refresh_token": str(payload.get("refresh_token") or ""),
        "token_type": str(payload.get("token_type") or "Bearer"),
        "expires_at": time.time() + float(payload.get("expires_in") or 3600),
        "obtained_at": time.time(),
    }
    if payload.get("id_token"):
        rec["id_token"] = str(payload["id_token"])
    if payload.get("api_key"):               # codex 用 id_token 换到的 API key
        rec["api_key"] = str(payload["api_key"])
    if not rec["access_token"] and not rec.get("api_key"):
        raise ValueError("token 端点响应缺少 access_token")
    store = load_store(root)
    store[name] = rec
    save_store(root, store)
    return rec


def _refresh_lock(name: str) -> threading.Lock:
    with _REFRESH_LOCKS_GUARD:
        return _REFRESH_LOCKS.setdefault(name, threading.Lock())


def _do_refresh(root: Path, name: str, rec: dict[str, Any]) -> dict[str, Any]:
    """用 refresh_token 换新 token；失败时保留旧记录并抛错。"""
    spec = OAUTH_PROVIDERS[name]
    data = {"client_id": spec["client_id"],
            "grant_type": "refresh_token",
            "refresh_token": rec["refresh_token"]}
    r = requests.post(spec["token_url"], data=data,
                      headers=spec.get("extra_headers") or {},
                      timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"刷新 token 失败（HTTP {r.status_code}），请重新授权")
    out = _save_tokens(root, name, r.json())
    # 有的端点刷新时不下发新 refresh_token：沿用旧的，避免下次无法刷新
    if not out["refresh_token"] and rec.get("refresh_token"):
        out["refresh_token"] = rec["refresh_token"]
        store = load_store(root)
        store[name] = out
        save_store(root, store)
    log.info("OAuth token 已刷新: %s", name)
    return out


def get_access_token(root: Path, name: str) -> str:
    """ProviderConfig 取 key 的入口：有有效 token 就返回，临期自动刷新。

    codex 优先返回用 id_token 换来的 API key（可直接打 api.openai.com/v1）；
    换不到时退回 OAuth access_token（只对 ChatGPT 后端有效，由调用方甄别）。
    """
    if name not in OAUTH_PROVIDERS:
        return ""
    rec = load_store(root).get(name)
    if not rec:
        return ""
    if rec.get("api_key"):
        return str(rec["api_key"])
    exp = float(rec.get("expires_at") or 0)
    if exp - time.time() > _REFRESH_MARGIN:
        return str(rec.get("access_token") or "")
    if not rec.get("refresh_token"):
        return str(rec.get("access_token") or "") if exp > time.time() else ""
    with _refresh_lock(name):
        # 拿锁后再读一次：可能别的线程刚刷新完
        rec = load_store(root).get(name) or {}
        if float(rec.get("expires_at") or 0) - time.time() > _REFRESH_MARGIN:
            return str(rec.get("api_key") or rec.get("access_token") or "")
        try:
            rec = _do_refresh(root, name, rec)
        except Exception as exc:             # noqa: BLE001
            log.warning("OAuth 刷新失败 %s: %s", name, exc)
            return ""
        return str(rec.get("api_key") or rec.get("access_token") or "")


def disconnect(root: Path, name: str) -> bool:
    store = load_store(root)
    if name in store:
        del store[name]
        save_store(root, store)
        return True
    return False


def authorized(root: Path, name: str) -> bool:
    rec = load_store(root).get(name) or {}
    return bool(rec.get("access_token") or rec.get("api_key"))


# ── 设备码流程 ────────────────────────────────────────────────
def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def start_device_flow(root: Path, name: str) -> dict[str, Any]:
    spec = OAUTH_PROVIDERS.get(name)
    if not spec or spec["flow"] != "device":
        raise ValueError(f"{name} 不支持设备码授权")
    data = {"client_id": spec["client_id"]}
    verifier = ""
    if spec.get("pkce_in_poll"):
        verifier, challenge = _pkce_pair()
        data["code_challenge"] = challenge
        data["code_challenge_method"] = "S256"
    r = requests.post(spec["device_url"], data=data,
                      headers=spec.get("extra_headers") or {},
                      timeout=_TIMEOUT)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise RuntimeError(f"设备授权请求失败（HTTP {r.status_code}）：{r.text[:200]}")
    flow_id = secrets.token_hex(8)
    flow = {
        "id": flow_id, "provider": name, "status": "pending", "error": "",
        "user_code": str(body.get("user_code") or ""),
        "verification_uri": str(body.get("verification_uri") or ""),
        "verification_uri_complete": str(body.get("verification_uri_complete") or ""),
        "interval": max(int(body.get("interval") or 5), 2),
        "expires_in": int(body.get("expires_in") or 900),
        "device_code": str(body.get("device_code") or ""),
        "code_verifier": verifier,
        "started_at": time.time(),
        "cancel": threading.Event(),
    }
    with _FLOWS_LOCK:
        _FLOWS[flow_id] = flow
    threading.Thread(target=_poll_device, args=(root, name, flow),
                     daemon=True, name=f"oauth-poll-{name}").start()
    # 返回值不含 device_code / verifier（bearer 等价物，不出后端）
    return {k: flow[k] for k in
            ("id", "provider", "status", "user_code",
             "verification_uri", "verification_uri_complete", "expires_in")}


def _poll_device(root: Path, name: str, flow: dict[str, Any]) -> None:
    spec = OAUTH_PROVIDERS[name]
    deadline = time.time() + flow["expires_in"]
    interval = flow["interval"]
    while time.time() < deadline and not flow["cancel"].is_set():
        try:
            data = {"client_id": spec["client_id"],
                    "device_code": flow["device_code"],
                    "grant_type": _DEVICE_GRANT}
            if flow.get("code_verifier"):
                data["code_verifier"] = flow["code_verifier"]
            r = requests.post(spec["token_url"], data=data,
                              headers=spec.get("extra_headers") or {},
                              timeout=_TIMEOUT)
            if r.status_code == 200:
                _save_tokens(root, name, r.json())
                flow["status"] = "ok"
                log.info("OAuth 设备授权完成: %s", name)
                return
            err = str(r.json().get("error") or "") if r.content else ""
            if err == "slow_down":
                interval += 5
            elif err in ("expired_token", "access_denied"):
                flow["status"] = "failed"
                flow["error"] = "授权已过期或被拒绝，请重新发起"
                return
            # authorization_pending 等其余错误：按间隔继续等
        except Exception as exc:             # noqa: BLE001
            log.warning("设备码轮询异常 %s: %s", name, exc)
        flow["cancel"].wait(interval)
    if flow["status"] == "pending":
        flow["status"] = "failed"
        flow["error"] = "授权超时，请重新发起"


def cancel_flow(flow_id: str) -> bool:
    with _FLOWS_LOCK:
        flow = _FLOWS.get(flow_id)
    if not flow:
        return False
    flow["cancel"].set()
    if flow["status"] == "pending":
        flow["status"] = "cancelled"
    return True


def flow_status(root: Path, flow_id: str) -> dict[str, Any] | None:
    with _FLOWS_LOCK:
        flow = _FLOWS.get(flow_id)
    if not flow:
        return None
    out = {"id": flow_id, "provider": flow["provider"], "status": flow["status"],
           "error": flow["error"], "user_code": flow["user_code"],
           "verification_uri_complete": flow["verification_uri_complete"]}
    if flow["status"] == "ok":
        out["authorized"] = authorized(root, flow["provider"])
    return out


# ── localhost 回跳流程（Codex）─────────────────────────────────
def _build_authorize_url(spec: dict[str, Any], redirect_uri: str,
                         challenge: str, state: str) -> str:
    q = {"response_type": "code", "client_id": spec["client_id"],
         "redirect_uri": redirect_uri, "scope": spec["scope"],
         "code_challenge": challenge, "code_challenge_method": "S256",
         "state": state}
    q.update(spec.get("extra_params") or {})
    return f"{spec['authorize_url']}?{urlencode(q)}"


def start_callback_flow(root: Path, name: str) -> dict[str, Any]:
    spec = OAUTH_PROVIDERS.get(name)
    if not spec or spec["flow"] != "callback":
        raise ValueError(f"{name} 不支持回跳授权")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    flow_id = secrets.token_hex(8)
    flow = {"id": flow_id, "provider": name, "status": "pending", "error": "",
            "user_code": "", "verification_uri_complete": "",
            "started_at": time.time(), "cancel": threading.Event()}
    with _FLOWS_LOCK:
        _FLOWS[flow_id] = flow
    threading.Thread(target=_run_callback_server,
                     args=(root, name, spec, flow, state, verifier, challenge),
                     daemon=True, name=f"oauth-cb-{name}").start()
    # 等服务器绑定端口拿到真实 URL（最多 3 秒）
    for _ in range(60):
        if flow.get("authorize_url") or flow["status"] == "failed":
            break
        time.sleep(0.05)
    out = {k: flow[k] for k in ("id", "provider", "status", "error")}
    out["authorize_url"] = flow.get("authorize_url", "")
    return out


def _run_callback_server(root: Path, name: str, spec: dict[str, Any],
                         flow: dict[str, Any], state: str,
                         verifier: str, challenge: str) -> None:
    server: HTTPServer | None = None
    port_used = 0
    for port in spec["ports"]:
        try:
            server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
            port_used = port
            break
        except OSError:
            continue
    if server is None:
        flow["status"] = "failed"
        flow["error"] = f"端口 {spec['ports']} 都被占用，无法接收授权回跳"
        return
    redirect_uri = f"http://localhost:{port_used}{spec['redirect_path']}"
    flow["authorize_url"] = _build_authorize_url(spec, redirect_uri, challenge, state)
    server.timeout = 2
    deadline = time.time() + 300
    done = threading.Event()

    def _exchange(code: str) -> str:
        r = requests.post(spec["token_url"], data={
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": redirect_uri, "client_id": spec["client_id"],
            "code_verifier": verifier}, timeout=_TIMEOUT)
        if r.status_code != 200:
            raise RuntimeError(f"授权码换 token 失败（HTTP {r.status_code}）")
        payload = dict(r.json())
        # Codex：用 id_token 换一把能直接打 api.openai.com 的 API key
        if payload.get("id_token"):
            try:
                ex = requests.post(spec["token_url"], data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": spec["client_id"],
                    "requested_token": "openai-api-key",
                    "subject_token": payload["id_token"],
                    "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                }, timeout=_TIMEOUT)
                if ex.status_code == 200 and ex.json().get("access_token"):
                    payload["api_key"] = ex.json()["access_token"]
            except Exception as exc:         # noqa: BLE001
                log.warning("id_token 换 API key 失败（仍保留 OAuth token）: %s", exc)
        _save_tokens(root, name, payload)
        return ""

    def _handle() -> None:
        while time.time() < deadline and not done.is_set() and not flow["cancel"].is_set():
            server.handle_request()          # timeout=2，循环检查取消/截止

    # 把 exchange 挂到 handler 上（handler 是类，按请求创建实例）
    _CallbackHandler.flow_ctx = {
        "path": spec["redirect_path"], "state": state,
        "exchange": _exchange, "done": done, "flow": flow,
    }
    try:
        t = threading.Thread(target=_handle, daemon=True)
        t.start()
        t.join(timeout=310)
        if flow["status"] == "pending":
            flow["status"] = "failed"
            flow["error"] = "等待授权回跳超时，请重新发起"
    finally:
        server.server_close()
        _CallbackHandler.flow_ctx = None
    if done.is_set() and flow["status"] == "pending":
        flow["status"] = "ok"
        log.info("OAuth 回跳授权完成: %s", name)


class _CallbackHandler(BaseHTTPRequestHandler):
    flow_ctx: dict[str, Any] | None = None

    def log_message(self, *a: Any) -> None:   # 静默：query 里有授权码，不进日志
        pass

    def _reply(self, code: int, text: str) -> None:
        body = (f"<html><meta charset='utf-8'><body style='font-family:sans-serif;"
                f"text-align:center;padding-top:3em'><h2>{text}</h2>"
                f"<p>可以关闭此页面，回到 Gantry。</p></body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:                 # noqa: N802
        ctx = _CallbackHandler.flow_ctx
        if not ctx:
            self._reply(400, "授权流程已结束")
            return
        u = urlparse(self.path)
        if u.path != ctx["path"]:
            self._reply(404, "未知路径")
            return
        q = parse_qs(u.query)
        if (q.get("state") or [""])[0] != ctx["state"]:
            # state 不匹配可能是别处的请求打错端口：不终止流程，只拒绝本次
            self._reply(400, "state 校验失败")
            return
        if q.get("error"):
            ctx["flow"]["status"] = "failed"
            ctx["flow"]["error"] = (q.get("error_description") or q["error"])[0]
            self._reply(200, f"授权失败：{ctx['flow']['error']}")
            ctx["done"].set()
            return
        code = (q.get("code") or [""])[0]
        if not code:
            self._reply(400, "缺少授权码")
            return
        try:
            ctx["exchange"](code)
            self._reply(200, "授权成功 ✓")
        except Exception as exc:             # noqa: BLE001
            ctx["flow"]["status"] = "failed"
            ctx["flow"]["error"] = str(exc)
            self._reply(200, f"授权失败：{exc}")
        ctx["done"].set()


# ── 状态汇总（给前端）──────────────────────────────────────────
def provider_status(root: Path, name: str) -> dict[str, Any]:
    spec = OAUTH_PROVIDERS.get(name)
    if not spec:
        return {"supported": False}
    rec = load_store(root).get(name) or {}
    with _FLOWS_LOCK:
        active = next((f for f in _FLOWS.values()
                       if f["provider"] == name and f["status"] == "pending"), None)
    return {
        "supported": True, "flow": spec["flow"], "label": spec["label"],
        "authorized": bool(rec.get("access_token") or rec.get("api_key")),
        "expires_at": rec.get("expires_at", 0),
        "has_refresh": bool(rec.get("refresh_token")),
        "obtained_at": rec.get("obtained_at", 0),
        "pending_flow": active["id"] if active else "",
    }
