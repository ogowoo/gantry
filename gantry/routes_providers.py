"""routes_providers.py — 模型提供商配置（设置页「模型提供商」）

只做请求/响应适配 + 校验：真正的合并语义在 ``providers.build_providers``，
这里负责把 config.json 的 ``providers.<name>`` 段读出来、改好、原子写回，
再重建内存里的 ``b.PROVIDERS``（``/v1/models`` 与模型选择器据此动态生成）。

约定：
- **API Key 只进不出**：列表只回 ``api_key_set`` 与 ``api_key_env``，永不回明文；
  `POST` 里不带 ``api_key`` = 保持原值，显式传空串 = 清除。
- 预设（openai/deepseek/kimi/grok…）不能被删除，改它们等于写覆盖层；
  「删除」对有覆盖的预设是**恢复预设默认**。
- 本地进程内 provider（``backend: "local"``）不走 HTTP，测试连接只检查 GGUF 文件。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from . import bridge as b
from . import providers as providers_mod

router = APIRouter()

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}$")
_RESERVED = {"copilot"}                       # 网页自动化通道，不是 HTTP 提供商
_JSON_MODES = {"auto", "json_schema", "json_object", "off"}
_APIS = {"openai", "anthropic"}
# 允许通过本接口写入的键（其余键一律拒绝，避免把任意东西塞进 providers）
_EDITABLE = {"base_url", "api", "api_key", "api_key_env", "models", "description",
             "max_tokens", "timeout_seconds", "json_mode", "vision",
             "proxy", "use_system_proxy", "backend", "model_path"}


def _cfg_path() -> Path:
    return Path(b.BASE) / "config.json"


def _read_cfg() -> dict[str, Any]:
    p = _cfg_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8") or "{}")
        except Exception as exc:                 # noqa: BLE001
            raise HTTPException(500, f"config.json 解析失败：{str(exc)[:160]}") from exc
    return json.loads(json.dumps(b.CFG, ensure_ascii=False))


def _write_cfg(cfg: dict[str, Any]) -> None:
    from .tool_plugins._atomic import atomic_write_text

    atomic_write_text(_cfg_path(), json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def _reload() -> None:
    """重建 PROVIDERS（失败保留旧对象，不影响已跑任务）。"""
    from .routes_config import _reload_providers

    _reload_providers()


def _env_value(name: str) -> str:
    try:
        return str(providers_mod._read_env(name) or "")
    except Exception:                            # noqa: BLE001
        return ""


def _env_names(entry: dict[str, Any]) -> list[str]:
    spec = entry.get("api_key_env", "")
    if isinstance(spec, (list, tuple)):
        return [str(x).strip() for x in spec if str(x).strip()]
    return [str(spec).strip()] if str(spec).strip() else []


def _entry_view(name: str, entry: dict[str, Any], *, custom: bool,
                preset: dict[str, Any] | None) -> dict[str, Any]:
    envs = _env_names(entry)
    active_env = next((e for e in envs if _env_value(e)), "")
    in_config = bool(str(entry.get("api_key") or "").strip())
    backend = str(entry.get("backend") or "").lower()
    is_local = backend == "local" or bool(entry.get("model_path"))
    models = [str(m) for m in (entry.get("models") or [])]
    base_url = str(entry.get("base_url") or "")
    if preset and preset.get("base_url") and custom:
        base_url = base_url or str(preset.get("base_url") or "")
    return {
        "name": name,
        "kind": "local" if is_local else ("custom" if custom else "preset"),
        "description": str(entry.get("description") or (preset or {}).get("description") or name),
        "base_url": base_url,
        "api": str(entry.get("api") or "openai").lower(),
        "api_key_env": envs,
        "api_key_env_active": active_env,
        "api_key_set": in_config or bool(active_env),
        "api_key_in_config": in_config,
        "backend": backend or "http",
        "model_path": str(entry.get("model_path") or ""),
        "models": models,
        "max_tokens": int(entry.get("max_tokens") or 0),
        "timeout_seconds": float(entry.get("timeout_seconds") or 300),
        "json_mode": str(entry.get("json_mode") or "auto"),
        "vision": entry.get("vision"),
        # 覆盖了预设的哪些键（界面用来显示"已改过"并提供恢复）
        "overrides": sorted(k for k in (entry.keys() if custom else ())
                            if preset is None or entry.get(k) != preset.get(k)),
        "configured": bool(is_local and entry.get("model_path"))
        or bool(base_url and (in_config or bool(active_env))),
    }


@router.get("/v1/providers")
async def list_providers():
    """全部提供商（预设 + config.json 新增/覆盖 + 本地进程内）。"""
    cfg = _read_cfg()
    custom = {k: v for k, v in (cfg.get("providers") or {}).items()
              if isinstance(v, dict)}
    merged = {k: dict(v) for k, v in providers_mod.PRESETS.items()}
    for name, entry in custom.items():
        merged.setdefault(name, {}).update(entry)
    out = []
    for name, entry in merged.items():
        preset = providers_mod.PRESETS.get(name)
        view = _entry_view(name, entry, custom=name in custom, preset=preset)
        out.append(view)
    order = {"local": 0, "custom": 1, "preset": 2}
    out.sort(key=lambda x: (order.get(x["kind"], 3), x["name"]))
    return {"providers": out, "default_model": str(b.CFG.get("default_model") or ""),
            "presets": sorted(providers_mod.PRESETS.keys()), "count": len(out)}


def _norm_models(raw: Any) -> list[str]:
    if isinstance(raw, str):
        items = re.split(r"[,;\n]+", raw)
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        raise HTTPException(400, "models 需要字符串或数组")
    out, seen = [], set()
    for it in items:
        s = it.strip()
        if not s or s in seen:
            continue
        if len(s) > 200:
            raise HTTPException(400, f"模型名过长：{s[:40]}…")
        seen.add(s)
        out.append(s)
    if len(out) > 200:
        raise HTTPException(400, "一个提供商的模型数上限 200")
    return out


def _norm_env(raw: Any) -> Any:
    if isinstance(raw, str):
        names = [x.strip() for x in re.split(r"[,;\s]+", raw) if x.strip()]
    elif isinstance(raw, (list, tuple)):
        names = [str(x).strip() for x in raw if str(x).strip()]
    else:
        raise HTTPException(400, "api_key_env 需要字符串或数组")
    for n in names:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", n):
            raise HTTPException(400, f"环境变量名不合法：{n}")
    if not names:
        return ""
    return names[0] if len(names) == 1 else names


@router.post("/v1/providers")
async def upsert_provider(payload: dict | None = None):
    """新增或修改一个提供商（写 config.json 的 providers.<name>）。"""
    data = payload or {}
    name = str(data.get("name") or "").strip().lower()
    if not name:
        raise HTTPException(400, "缺少 name")
    if not _NAME_RE.match(name):
        raise HTTPException(400, "名称只能是小写字母/数字/._-，且以字母或数字开头（≤41 字符）")
    if name in _RESERVED:
        raise HTTPException(400, f"{name} 是内置通道名，不能用作提供商名")

    unknown = sorted(set(data.keys()) - _EDITABLE - {"name", "kind"})
    if unknown:
        raise HTTPException(400, f"不支持的字段：{unknown}")

    cfg = _read_cfg()
    provs = cfg.setdefault("providers", {})
    if not isinstance(provs, dict):
        provs = {}
        cfg["providers"] = provs
    existed = isinstance(provs.get(name), dict)
    entry: dict[str, Any] = dict(provs.get(name) or {})

    if "base_url" in data:
        url = str(data.get("base_url") or "").strip().rstrip("/")
        if url and not re.match(r"^https?://", url, re.I):
            raise HTTPException(400, "base_url 必须是 http(s):// 地址")
        if url:
            entry["base_url"] = url
        else:
            entry.pop("base_url", None)

    if "api" in data or "backend" in data:
        api = str(data.get("api") or entry.get("api") or "openai").lower()
        backend = str(data.get("backend") or entry.get("backend") or "").lower()
        if backend and backend not in ("local", "http", ""):
            raise HTTPException(400, "backend 只支持 http 或 local")
        if api not in _APIS:
            raise HTTPException(400, "api 只支持 openai 或 anthropic")
        entry["api"] = api
        if backend == "http" or not backend:
            entry.pop("backend", None)
        else:
            entry["backend"] = backend

    if "model_path" in data:
        mp = str(data.get("model_path") or "").strip()
        if mp:
            entry["model_path"] = mp
            entry["backend"] = "local"
        else:
            entry.pop("model_path", None)

    if "api_key_env" in data:
        env = _norm_env(data.get("api_key_env"))
        if env:
            entry["api_key_env"] = env
        else:
            entry.pop("api_key_env", None)

    if "api_key" in data:
        key = str(data.get("api_key") or "").strip()
        if key:
            if len(key) > 400:
                raise HTTPException(400, "api_key 过长")
            entry["api_key"] = key
        else:
            entry.pop("api_key", None)

    if "models" in data:
        models = _norm_models(data.get("models"))
        if models:
            entry["models"] = models
        else:
            entry.pop("models", None)

    if "description" in data:
        desc = str(data.get("description") or "").strip()
        if len(desc) > 300:
            raise HTTPException(400, "description 过长（≤300 字）")
        if desc:
            entry["description"] = desc
        else:
            entry.pop("description", None)

    if "max_tokens" in data:
        try:
            mt = int(data.get("max_tokens") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "max_tokens 需要整数")
        if mt < 0 or mt > 2_000_000:
            raise HTTPException(400, "max_tokens 超出范围（0–2000000，0=不发送）")
        entry["max_tokens"] = mt

    if "timeout_seconds" in data:
        try:
            to = float(data.get("timeout_seconds") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "timeout_seconds 需要数字")
        if to < 5 or to > 7200:
            raise HTTPException(400, "timeout_seconds 超出范围（5–7200 秒）")
        entry["timeout_seconds"] = to

    if "json_mode" in data:
        jm = str(data.get("json_mode") or "auto").lower()
        if jm not in _JSON_MODES:
            raise HTTPException(400, f"json_mode 只支持 {sorted(_JSON_MODES)}")
        entry["json_mode"] = jm

    if "vision" in data:
        v = data.get("vision")
        if v is None:
            entry.pop("vision", None)
        else:
            entry["vision"] = bool(v)

    for flag in ("proxy", "use_system_proxy"):
        if flag in data:
            if flag == "proxy":
                val = str(data.get("proxy") or "").strip()
                if val:
                    entry["proxy"] = val
                else:
                    entry.pop("proxy", None)
            else:
                entry["use_system_proxy"] = bool(data.get("use_system_proxy"))

    # 预设名 + 空覆盖层 = 没有意义的条目，删掉（等价恢复预设）
    preset = providers_mod.PRESETS.get(name)
    if preset is not None and not entry:
        provs.pop(name, None)
    else:
        provs[name] = entry
    _write_cfg(cfg)

    b.CFG.setdefault("providers", {})[name] = json.loads(json.dumps(entry))
    _reload()
    provider = (b.PROVIDERS or {}).get(name)
    view = _entry_view(name, entry, custom=name in provs, preset=preset)
    return {"ok": True, "created": not existed, "provider": view,
            "configured": bool(getattr(provider, "configured", False))}


@router.delete("/v1/providers/{name}")
async def delete_provider(name: str):
    """删除自定义提供商；对预设则是删除覆盖层、恢复预设默认。"""
    key = str(name or "").strip().lower()
    cfg = _read_cfg()
    provs = cfg.get("providers")
    if not isinstance(provs, dict) or key not in provs:
        raise HTTPException(404, f"没有可删除的配置：{key or '(空)'}（预设未被覆盖）")
    provs.pop(key, None)
    _write_cfg(cfg)
    b.CFG.setdefault("providers", {}).pop(key, None)
    _reload()
    is_preset = key in providers_mod.PRESETS
    return {"ok": True, "name": key, "restored_preset": is_preset,
            "deleted": not is_preset}


@router.post("/v1/providers/{name}/test")
async def test_provider(name: str):
    """轻量连通性检查：GET {base_url}/models（本地则检查 GGUF 文件）。

    不发送任何用户内容，不消耗生成额度；返回状态码、耗时与模型数。
    """
    key = str(name or "").strip().lower()
    provider = (b.PROVIDERS or {}).get(key)
    if provider is None:
        raise HTTPException(404, f"提供商不存在：{key or '(空)'}")
    t0 = time.time()

    if str(getattr(provider, "backend", "")).lower() == "local":
        mp = str(getattr(provider, "model_path", "") or "")
        ok = bool(mp) and Path(mp).is_file()
        return {"ok": ok, "kind": "local", "latency_ms": int((time.time() - t0) * 1000),
                "path": mp,
                "error": "" if ok else (f"模型文件不存在：{mp}" if mp else "未配置 model_path")}

    base_url = str(getattr(provider, "base_url", "") or "")
    if not base_url:
        return {"ok": False, "kind": "http", "latency_ms": 0, "error": "未配置 base_url"}
    if not getattr(provider, "api_key", ""):
        return {"ok": False, "kind": "http", "latency_ms": 0,
                "error": "未配置 API Key（填写 key 或设置环境变量 "
                         f"{', '.join(_env_names({'api_key_env': getattr(provider, 'api_key_envs', [])})) or '(未指定)'}）"}
    try:
        import httpx

        headers = {"Authorization": f"Bearer {provider.api_key}"}
        path = "/models"
        if str(getattr(provider, "api", "openai")).lower() == "anthropic":
            headers = {"x-api-key": provider.api_key, "anthropic-version": "2023-06-01"}
            path = "/v1/models"
        proxy = getattr(provider, "proxy_url", None)
        kwargs: dict[str, Any] = {"timeout": 12.0, "headers": headers}
        px = proxy() if callable(proxy) else None
        if px:
            kwargs["proxy"] = px
        with httpx.Client(base_url=base_url, **kwargs) as client:
            resp = client.get(path)
        ms = int((time.time() - t0) * 1000)
        if resp.status_code == 401 or resp.status_code == 403:
            return {"ok": False, "kind": "http", "status": resp.status_code, "latency_ms": ms,
                    "error": "认证失败：API Key 无效或无权限"}
        if resp.status_code >= 400:
            return {"ok": False, "kind": "http", "status": resp.status_code, "latency_ms": ms,
                    "error": f"服务返回 {resp.status_code}（该服务可能不支持 /models，"
                             "可直接保存后发一条短消息验证）"}
        try:
            data = resp.json()
        except Exception:                        # noqa: BLE001
            data = {}
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            items = data.get("models") if isinstance(data, dict) else None
        names = []
        for it in (items or [])[:30]:
            if isinstance(it, dict):
                names.append(str(it.get("id") or it.get("name") or ""))
        return {"ok": True, "kind": "http", "status": resp.status_code, "latency_ms": ms,
                "models": [n for n in names if n], "models_count": len(items or [])}
    except Exception as exc:                     # noqa: BLE001
        text = ""
        try:
            text = provider.net_error_text(exc)
        except Exception:                        # noqa: BLE001
            text = str(exc)[:200]
        return {"ok": False, "kind": "http", "latency_ms": int((time.time() - t0) * 1000),
                "error": text[:300]}


@router.post("/v1/providers/default-model")
async def set_default_model(payload: dict | None = None):
    """设置全局默认模型（"provider/model"），立即生效并落库。"""
    model = str((payload or {}).get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少 model")
    pm = providers_mod.split_provider_model(model)
    if pm and pm[0] not in (b.PROVIDERS or {}):
        raise HTTPException(400, f"提供商不存在：{pm[0]}")
    if pm:
        prov = b.PROVIDERS[pm[0]]
        known = [str(m) for m in getattr(prov, "models", [])]
        if known and pm[1] not in known:
            raise HTTPException(400, f"{pm[0]} 的模型列表里没有 {pm[1]}")
    cfg = _read_cfg()
    cfg["default_model"] = model
    _write_cfg(cfg)
    b.CFG["default_model"] = model
    b.DEFAULT_MODEL = model                # 全局立即生效（否则要重启才换默认模型）
    return {"ok": True, "default_model": model}
