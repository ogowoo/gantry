"""deepseek-web 插件：把 DeepSeek 网页版的反代接成 CBP 的一个提供商。

**为什么要插件而不是改核心**
CBP 已经有"OpenAI 兼容提供商"这层抽象（`gantry/providers.py`），DeepSeek 网页版反代
（[deepseek-free-api](https://github.com/Fly143/deepseek-free-api)、
[ds2api](https://github.com/CJackHwang/ds2api) 之类）对外正好也是 OpenAI 协议，
所以**不需要碰 Copilot 那套浏览器桥接**，只要：

1. 起一个反代（Docker / python，默认 `http://127.0.0.1:8000/v1`）；
2. 本插件把它注册成提供商 `dsweb`（模型 ID 形如 `dsweb/deepseek-reasoner`）；
3. 面板里看健康状态与实时模型列表。

**为什么不自己逆向**
DeepSeek 网页端要 PoW（`DeepSeekHashV1`，官方 WASM 求解）+ 浏览器 TLS 指纹
（curl_cffi）+ token 24h 刷新；这些已有成熟实现，重复做没有收益，而且**封号风险**
由反代自己承担（实测惩罚会从 1 天 → 3 天 → 8 天 → 永久）。

**配置**（plugin.json 的 `gantry.config`，也可在插件管理的配置里改）
- `base_url`：反代地址（默认 `http://127.0.0.1:8000/v1`）
- `api_key`：反代的客户端 Key（多数反代随便填，或用它面板里创建的那个）
- `provider`：模型前缀（默认 `dsweb`）
- `models`：先用这份兜底列表；`api/status` 探测成功时会返回反代实际提供的模型
- `timeout_seconds`：单次请求超时（网页反代非流式要等完整回复，别设太小）

接口：`GET /v1/plugins/deepseek-web/api/status` → 反代连通性 + 模型列表
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

DEFAULTS = {
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key": "sk-dsapi",
    "provider": "dsweb",
    "models": ["deepseek-default", "deepseek-reasoner", "deepseek-search",
               "deepseek-expert-reasoner", "deepseek-vision"],
    "timeout_seconds": 300,
}

_LAST: dict[str, Any] = {"at": 0.0, "ok": False, "models": [], "error": "", "latency_ms": 0}


def normalize_models(payload: Any) -> list[str]:
    """从 OpenAI `/v1/models` 的返回里取模型 ID（容忍各种形状）。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        data = payload.get("models") if isinstance(payload.get("models"), list) else []
    out: list[str] = []
    for item in data:
        mid = ""
        if isinstance(item, str):
            mid = item
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "")
        mid = mid.strip()
        if mid and mid not in out:
            out.append(mid)
    return out


async def probe(base_url: str, api_key: str, timeout: float = 4.0) -> dict[str, Any]:
    """探测反代：`GET {base_url}/models`。返回 {ok, models, error, latency_ms}。"""
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            return {"ok": False, "models": [], "latency_ms": ms,
                    "error": f"HTTP {resp.status_code}: {resp.text[:160]}"}
        try:
            payload = resp.json()
        except Exception:
            payload = {}
        return {"ok": True, "models": normalize_models(payload), "latency_ms": ms, "error": ""}
    except Exception as exc:  # 反代没起 / 端口不通 / 超时
        # httpx 的连接错误常常是空消息，退化成类名，面板上至少知道是什么错
        msg = str(exc).strip() or type(exc).__name__
        return {"ok": False, "models": [], "error": msg[:200],
                "latency_ms": int((time.monotonic() - t0) * 1000)}


def probe_sync(base_url: str, api_key: str, timeout: float = 1.5) -> dict[str, Any]:
    """同步探测（setup 里用；主机不会 await 协程，所以不能在这里用 async）。"""
    import json as _json
    import urllib.error
    import urllib.request

    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = _json.loads(resp.read() or b"{}")
        return {"ok": True, "models": normalize_models(payload), "error": "",
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "models": [], "latency_ms": 0,
                "error": f"HTTP {exc.code}: {str(exc.reason)[:120]}"}
    except Exception as exc:
        return {"ok": False, "models": [], "latency_ms": 0,
                "error": (str(exc).strip() or type(exc).__name__)[:200]}


_REFRESH: dict[str, Any] = {"thread": None, "stop": False, "models": []}


def _refresh_loop(ctx: Any, interval: float = 20.0) -> None:
    """后台轮询反代模型；只在**模型集合变化**时重新注册（避免刷爆 registered 列表）。"""
    import time as _time

    while not _REFRESH["stop"]:
        try:
            _time.sleep(interval)
            if _REFRESH["stop"]:
                return
            cfg = _cfg(ctx)
            res = probe_sync(str(cfg["base_url"]), str(cfg["api_key"]), timeout=2.0)
            live = list(res["models"])
            if not live or live == _REFRESH["models"]:
                continue
            ctx.register_provider(str(cfg["provider"]), {
                "base_url": str(cfg["base_url"]),
                "api_key": str(cfg["api_key"]),
                "models": live,
                "api": "openai",
                "timeout_seconds": int(cfg["timeout_seconds"]),
                "description": f"DeepSeek 网页版反代（{cfg['base_url']}）",
            })
            _REFRESH["models"] = live
            ctx.log(f"deepseek-web：模型已更新 {live}")
        except Exception:
            continue


try:  # 插件可独立加载：优先用 host 的 brand.env
    from gantry.brand import env
except Exception:  # pragma: no cover
    import os as _os

    def env(name, default=None):
        return (_os.environ.get("GANTRY_" + name)
                or _os.environ.get("CBP_" + name) or default)


def _start_refresher(ctx: Any, interval: float) -> None:
    import threading

    if env("PLUGIN_NO_TIMERS") == "1":
        return
    if interval <= 0:
        return
    if _REFRESH["thread"] is not None and _REFRESH["thread"].is_alive():
        return
    _REFRESH["stop"] = False
    t = threading.Thread(target=_refresh_loop, args=(ctx, interval),
                         name="dsweb-model-refresh", daemon=True)
    _REFRESH["thread"] = t
    t.start()


def _cfg(ctx: Any) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    raw = getattr(ctx, "config", None)
    if isinstance(raw, dict):
        for k, v in raw.items():
            if v not in (None, "", []):
                cfg[k] = v
    return cfg


async def _status(ctx: Any) -> dict[str, Any]:
    """面板/接口共用：反代连通性 + 模型清单（模型归属插件前缀，便于直接选）。

    反代挂掉时每次探测 ≈2.3s（httpx 全地址重试），面板每开一次就卡一次。
    所以**有缓存先回缓存**（后台异步再探测一次，完成即覆盖缓存），只有
    首次（无缓存）才同步探测——面板打开不被一个挂掉的反代拖住。
    """
    cfg = _cfg(ctx)

    def _payload(cached: bool) -> dict[str, Any]:
        prefix = str(cfg["provider"])
        ok = bool(_LAST.get("ok"))
        live = list(_LAST.get("models") or [])
        return {
            "provider": prefix,
            "base_url": cfg["base_url"],
            "reachable": ok,
            "ready": bool(ok and live),
            "latency_ms": _LAST.get("latency_ms") or 0,
            "error": _LAST.get("error") or "",
            "models_in_proxy": live,
            "model_ids": [f"{prefix}/{m}" for m in live],
            "suggested_models": list(cfg["models"]),
            "cached": cached,
            "hint": (f"选模型时用 {prefix}/<模型名>" if (ok and live) else
                     ("反代在线但没有可用模型：打开它的管理面板登录 DeepSeek 账号后刷新"
                      if ok else
                      f"反代连不上（{cfg['base_url']}）：先把 docker/python 那个反代跑起来")),
        }

    if _LAST.get("at"):
        asyncio.get_running_loop().create_task(_refresh_status(ctx, cfg))
        return _payload(cached=True)

    res = await probe(str(cfg["base_url"]), str(cfg["api_key"]), timeout=2.0)
    _apply_probe(ctx, cfg, res)
    return _payload(cached=False)


async def _refresh_status(ctx: Any, cfg: dict[str, Any]) -> None:
    """后台刷新 _LAST（不阻塞面板）；失败保留旧缓存。"""
    try:
        res = await probe(str(cfg["base_url"]), str(cfg["api_key"]), timeout=2.0)
        _apply_probe(ctx, cfg, res)
    except Exception:
        pass


def _apply_probe(ctx: Any, cfg: dict[str, Any], res: dict[str, Any]) -> None:
    """把一次探测结果落进 _LAST，并按实际清单重新注册提供商。"""
    # 反代**实际**给出的模型；配置里的清单只是"建议值"，不能当成已就绪
    live_models = list(res["models"])
    if res["ok"] and live_models:
        try:
            ctx.register_provider(str(cfg["provider"]), {
                "base_url": str(cfg["base_url"]),
                "api_key": str(cfg["api_key"]),
                "models": list(live_models),
                "api": "openai",
                "timeout_seconds": int(cfg["timeout_seconds"]),
                "description": f"DeepSeek 网页版反代（{cfg['base_url']}）",
            })
        except Exception:
            pass
    _LAST.update({"at": time.time(), "ok": res["ok"], "models": live_models,
                  "error": res["error"], "latency_ms": res["latency_ms"]})


def setup(ctx: Any) -> None:
    cfg = _cfg(ctx)
    prefix = str(cfg["provider"])
    # 先做一次很短（≤0.4s）的同步探测：本地反代通常立即返回；连不上/慢就
    # 不把兜底模型当成"已就绪"，交给后台刷新，避免插件页被 1.5s 连接超时拖住。
    quick = probe_sync(str(cfg["base_url"]), str(cfg["api_key"]), timeout=0.4)
    models = [str(m) for m in (quick["models"] if quick["ok"] else []) if str(m).strip()]
    ctx.register_provider(prefix, {
        "base_url": str(cfg["base_url"]),
        "api_key": str(cfg["api_key"]),
        "models": models,
        "api": "openai",
        "timeout_seconds": int(cfg["timeout_seconds"]),
        "description": f"DeepSeek 网页版反代（{cfg['base_url']}）",
    })
    ctx.register_api("GET", "api/status", lambda *_: _status(ctx))
    ctx.register_panel({
        "id": "status",
        "title": "DeepSeek 网页版反代",
        "view": "kv",
        "api": {"method": "GET", "path": "api/status"},
        "hint": "反代连不上就先把 docker/python 那个反代跑起来",
    })
    _REFRESH["models"] = list(models)
    ctx.log(f"deepseek-web 已注册提供商 {prefix}（{cfg['base_url']}，{len(models)} 个模型）")
    # 反代可能后起/登录后才有模型：后台每 20 秒看一眼，变了就重新注册
    _start_refresher(ctx, float(cfg.get("refresh_seconds", 20)))


def teardown(ctx: Any) -> None:
    _REFRESH["stop"] = True
    ctx.log("deepseek-web 已卸载（提供商随主机一起移除）")
