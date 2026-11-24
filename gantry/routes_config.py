"""routes_config.py — 设置与运行环境端点：配置读写、沙箱档位、插件、浏览器模式、工作区/目录选择、调试快照

从 api.py 拆出（第五批解耦）：只做请求/响应适配。共享状态与基础设施仍在
api.py，这里经 ``a.xxx`` 访问（保持测试对 api 模块的打桩语义）；
本模块用自带 APIRouter 注册路由，由 api.py 在末尾 include。
"""
from __future__ import annotations

import asyncio, hmac, json, logging, os, re, threading, time, uuid
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request as HttpRequest
from fastapi.responses import (StreamingResponse, FileResponse, RedirectResponse,
                               JSONResponse)
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from contextlib import AsyncExitStack

from . import api as a
from . import bridge as b
from . import i18n as backend_i18n
from . import model_store
from . import prompt_lang

router = APIRouter()


# ── 本地模型库：根目录 / 外部目录 / provider 路径写入 ─────────────────
def _models_root() -> Path:
    """模型库根目录：data/models（类别子目录 chat/embedding/rerank/mmproj）。"""
    return Path(b.BASE) / "data" / "models"


def _external_model_dirs() -> list[Path]:
    """当前 local provider 指向的库外目录：一起扫描，避免"文件在 E: 盘看不到"。"""
    out: list[Path] = []
    for p in (b.PROVIDERS or {}).values():
        if str(getattr(p, "backend", "")).lower() != "local":
            continue
        mp = str(getattr(p, "model_path", "") or "")
        if mp:
            parent = Path(mp).parent
            if parent.is_dir():
                out.append(parent)
    return out


def _set_provider_model_path(provider: str, path: str, kind: dict | None = None) -> dict:
    """把 model_path 写进 config.json 的 providers.<name>（原子写），并同步内存配置。"""
    from .tool_plugins._atomic import atomic_write_text

    name = str(provider or "").strip()
    if not name:
        raise HTTPException(400, "缺少 provider")
    cfg_path = Path(b.BASE) / "config.json"
    try:
        cfg = (json.loads(cfg_path.read_text(encoding="utf-8"))
               if cfg_path.exists() else dict(b.CFG))
    except Exception:                        # noqa: BLE001
        cfg = dict(b.CFG)
    provs = cfg.setdefault("providers", {})
    entry = provs.get(name)
    if not isinstance(entry, dict):
        entry = {}
        provs[name] = entry
    entry["backend"] = "local"
    entry["model_path"] = str(path)
    # models 列表与 provider 名字段保持一致，否则 /v1/models 里选不到它
    mid = Path(path).stem.lower().replace(" ", "-")
    models = [str(x) for x in (entry.get("models") or [])]
    if mid not in models:
        models.insert(0, mid)
    entry["models"] = models[:1] or [mid]
    if kind and kind.get("kind") == "chat":
        mm = model_store.pair_mmproj(path, _models_root())
        if mm and not entry.get("mmproj_path"):
            entry["mmproj_path"] = mm
    if not entry.get("description"):
        entry["description"] = "进程内 GGUF（模型库管理）"
    try:
        atomic_write_text(cfg_path, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(500, f"写 config.json 失败：{str(exc)[:160]}") from exc
    # 内存配置同步（/v1/config 与后续 build_providers 都读它）
    mem = b.CFG.setdefault("providers", {})
    mem_entry = mem.get(name)
    if not isinstance(mem_entry, dict):
        mem_entry = {}
        mem[name] = mem_entry
    mem_entry.update(entry)
    return {"model": mid, "mmproj": entry.get("mmproj_path", "")}


def _reload_providers() -> None:
    """按最新配置重建 PROVIDERS（原地替换，别的地方持的都是 b.PROVIDERS）。"""
    from . import providers as providers_mod

    try:
        fresh = providers_mod.build_providers(b.CFG)
    except Exception as exc:                 # noqa: BLE001
        b.log(f"重建 providers 失败（保留旧的）：{exc}")
        return
    b.PROVIDERS.clear()
    b.PROVIDERS.update(fresh)


@router.post("/v1/agent/debug/download")
async def agent_debug_download(payload: dict | None = None):
    """诊断：立即把当前页面的第一个下载候选存到 data/debug-download/。"""
    stem = str((payload or {}).get("stem") or "")
    # 点下载要驱动页面：独占一个窗口，免得把正在跑的任务页面点走
    try:
        async with b._window("", wait_timeout=10):
            return await b.debug_download_now(stem)
    except b.CopilotBusy as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/v1/agent/debug/panel")
async def agent_debug_panel():
    """诊断：Copilot 右侧面板（文件预览）候选块一览。"""
    return await b.debug_side_panel()


@router.get("/v1/agent/debug/page")
async def agent_debug_page(limit: int = 6000):
    """只读诊断：返回当前页面的纯文本尾部（排查界面语言/锚点，不改任何状态）。

    非任务上下文：直接读共享页，不占窗口、不导航。
    """
    try:
        url = b.current_page().url if b.current_page() else None
    except Exception:
        url = None
    try:
        text = await b.body_text()
    except Exception as exc:
        return {"url": url, "error": str(exc), "text": ""}
    lim = max(200, int(limit))
    return {"url": url, "len": len(text), "text": text[-lim:]}


@router.get("/v1/agent/sandbox")
async def get_sandbox():
    """查询当前沙箱档位与可选值。"""
    return {"mode": str(getattr(b.AGENT_TOOLS, "sandbox_mode", "workspace-write")),
            "modes": list(b.SANDBOX_MODES),
            "plan_mode": bool(getattr(b.AGENT_TOOLS, "plan_mode", False))}


@router.post("/v1/agent/sandbox")
async def set_sandbox(payload: dict | None = None):
    """切换沙箱档位：read-only / workspace-write / danger-full-access。"""
    mode = str((payload or {}).get("mode") or "").strip().lower()
    if mode not in b.SANDBOX_MODES:
        raise HTTPException(400, f"mode 必须是 {list(b.SANDBOX_MODES)} 之一")
    b.AGENT_TOOLS.sandbox_mode = mode
    b.AGENT_CFG["sandbox_mode"] = mode
    persist = bool((payload or {}).get("persist"))
    if persist:
        cfg_path = b.BASE / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.setdefault("agent", {})["sandbox_mode"] = mode
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        except Exception as exc:
            raise HTTPException(500, f"写 config.json 失败: {exc}")
    return {"mode": mode, "persisted": persist}


# ── 设置页：配置读写（白名单）──────────────────────────────────────
# 只允许改「有明确语义、不会把服务搞挂」的键；api_token 这类敏感项只读且打码。
CONFIG_SCHEMA: dict[str, dict] = {
    # 外观/界面（前端本地保存，这里只登记，便于设置页统一展示）
    # 提示词语言：决定发给模型的规则用什么语言写（auto = 跟随界面语言）
  "agent.prompt_language": {"kind": "enum", "values": ["auto", "zh", "tw", "en"],
                            "label": "提示词语言", "group": "exec"},
  # 表达风格：文件库在 personas/*.md，请求头 X-Gantry-Persona 可按会话覆盖
  "agent.persona": {"kind": "str", "group": "identity", "label": "默认 Persona（表达风格）"},
  # 子代理的 persona：默认 terse 省 token；填 inherit = 跟随默认链
  "agent.subagent_persona": {"kind": "str", "group": "identity",
                             "label": "子代理 Persona（默认 terse）"},
  # 工作区 personas/ 来自仓库，属提示词注入面；不信任仓库时关掉
  "agent.allow_workspace_personas": {"kind": "bool", "group": "identity",
                                     "label": "允许工作区 Persona 覆盖全局（仓库文件会进提示词）"},
  "sandbox_mode": {"kind": "enum", "values": ["read-only", "workspace-write",
                                                "danger-full-access"],
                     "group": "exec", "label": "权限档位（工具能改什么）"},
    "browser_startup": {"kind": "enum", "values": ["lazy", "eager"], "group": "browser",
                        "label": "浏览器启动方式"},
    "browser_width": {"kind": "int", "min": 800, "max": 3200, "group": "browser",
                      "label": "自动化浏览器窗口宽度"},
    "browser_height": {"kind": "int", "min": 600, "max": 1080, "group": "browser",
                       "label": "自动化浏览器窗口高度"},
    "headless": {"kind": "bool", "group": "browser", "label": "无头模式（不显示浏览器窗口）"},
    "auto_resume": {"kind": "bool", "group": "exec", "label": "服务重启后自动续跑任务"},
    "allow_copilot_file": {"kind": "bool", "group": "exec", "label": "允许 copilot_file 直读文件"},
    "diagnostics": {"kind": "bool", "group": "exec", "label": "保留诊断信息"},
    "spans_enabled": {"kind": "bool", "group": "exec", "label": "记录工具耗时区间"},
    "default_model": {"kind": "str", "group": "exec", "label": "默认模型"},
    "agent.auto_team_enabled": {"kind": "bool", "group": "exec",
                                "label": "自动派发子代理（待办≥2 且互不重叠时）"},
    "agent.rate_limit_suspend": {"kind": "bool", "group": "exec",
                                 "label": "Copilot 限流时挂起（到点自动继续，可换提供商模型）"},
    "agent.copilot_windows": {"kind": "int", "min": 1, "group": "exec",
                              "label": "Copilot 自动化窗口数（并行任务数；1=串行）"},
    "agent.todo_nudge_every": {"kind": "int", "min": 0, "max": 200, "group": "exec",
                               "label": "每 N 步提醒模型更新待办（0=关闭）"},
    "agent.subagent_parallel": {"kind": "int", "min": 1, "max": 8, "group": "exec",
                                "label": "子代理并发数"},
    "agent.subagent_max_steps": {"kind": "int", "min": 5, "max": 500, "group": "exec",
                                 "label": "子代理最多步数"},
    "agent.approval_scope": {"kind": "enum", "values": ["each", "session"], "group": "exec",
                             "label": "审批范围"},
    "plain_code_limit": {"kind": "int", "min": 500, "max": 50000, "group": "exec",
                         "label": "代码明文单次写入上限（字符，超出改用 Base64 分块）"},
    "agent.memory_nudge": {"kind": "bool", "default": True,
                           "label": "收尾提醒写记忆（做了实质工作却没记过时，拦一次 final）", "group": "exec"},
    "agent.context_token_budget": {"kind": "int", "min": 2000, "max": 2000000, "group": "exec",
                                   "label": "上下文预算（token）"},
    "agent.max_sessions": {"kind": "int", "min": 1, "max": 500, "group": "data",
                           "label": "最多保留会话数"},
    "agent.snapshot_retention": {"kind": "int", "min": 0, "max": 500, "group": "data",
                                 "label": "文件快照保留份数"},
    "agent.completed_session_retention_days": {"kind": "int", "min": 0, "max": 365,
                                               "group": "data", "label": "已完成会话保留天数"},

    # ── 危险区（设置页单列，默认关闭）─────────────────────────────
    "agent.allow_self_upgrade": {"kind": "bool", "default": False,
                           "label": "允许 Agent 修改服务自身代码（自升级）",
                           "group": "danger"},

    # ── 知识库 ──────────────────────────────────────────────────────────
    "kb.embedding.backend": {"kind": "enum",
                             "values": ["local", "fastembed", "http", "hash", "off"],
                             "group": "kb", "label": "Embedding 后端"},
    "kb.embedding.model": {"kind": "str", "group": "kb",
                           "label": "Embedding 模型（模型名 / GGUF 路径 / HTTP model id）"},
    "kb.ocr.engine": {"kind": "enum",
                      "values": ["auto", "rapidocr", "xiaomi"],
                      "group": "kb", "label": "OCR 引擎"},
}


def _cfg_get(path: str, default=None):
    node = b.CFG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _cfg_set(path: str, value) -> None:
    parts = path.split(".")
    node = b.CFG
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value


def _coerce(key: str, raw):
    spec = CONFIG_SCHEMA[key]
    kind = spec["kind"]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        try:
            val = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} 需要整数")
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and val < lo:
            raise HTTPException(400, f"{key} 不能小于 {lo}")
        if hi is not None and val > hi:
            raise HTTPException(400, f"{key} 不能大于 {hi}")
        return val
    if kind == "enum":
        val = str(raw).strip().lower()
        if val not in spec["values"]:
            raise HTTPException(400, f"{key} 必须是 {spec['values']} 之一")
        return val
    return str(raw)


@router.get("/v1/config")
async def get_config():
    """设置页读取：白名单键的当前值 + 规格（界面据此渲染控件）。"""
    values = {k: _cfg_get(k, b.CFG.get(k) if "." not in k else None) for k in CONFIG_SCHEMA}
    # 没配过的键显示**当前生效值**，避免输入框空着让人以为没生效（窗口数默认 1）
    if values.get("agent.copilot_windows") in (None, ""):
        try:
            values["agent.copilot_windows"] = b.WINDOWS.max_windows
        except Exception:
            values.setdefault("agent.copilot_windows", 1)
    secrets = {"api_token": "已设置" if b.CFG.get("api_token") else "未设置"}
    return {"values": values, "schema": CONFIG_SCHEMA, "readonly": secrets}


@router.post("/v1/config")
async def set_config(payload: dict | None = None):
    """设置页保存：只接受白名单键；persist=true 时写回 config.json。"""
    data = payload or {}
    incoming = data.get("values") or {}
    if not isinstance(incoming, dict) or not incoming:
        raise HTTPException(400, "values 不能为空")
    unknown = [k for k in incoming if k not in CONFIG_SCHEMA]
    if unknown:
        raise HTTPException(400, f"不支持修改这些键：{unknown}")
    applied = {k: _coerce(k, incoming[k]) for k in incoming}
    for key, val in applied.items():
        _cfg_set(key, val)
        if key == "sandbox_mode":
            b.AGENT_TOOLS.sandbox_mode = val
            b.AGENT_CFG["sandbox_mode"] = val
        elif key == "headless":
            b.AGENT_CFG["headless"] = val
        elif key.startswith("agent."):
            b.AGENT_CFG[key.split(".", 1)[1]] = val
            if key == "agent.allow_self_upgrade":
                # 即时生效：危险区开关一拨就能用，不用重启服务
                b.AGENT_TOOLS.allow_self_upgrade = val
        elif key == "auto_resume":
            b.AGENT_CFG["auto_resume"] = val

    persisted = False
    if data.get("persist", True):
        cfg_path = b.BASE / "config.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            for key, val in applied.items():
                node = cfg
                parts = key.split(".")
                for part in parts[:-1]:
                    nxt = node.get(part)
                    if not isinstance(nxt, dict):
                        nxt = {}
                        node[part] = nxt
                    node = nxt
                node[parts[-1]] = val
            cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            persisted = True
        except Exception as exc:
            raise HTTPException(500, f"写 config.json 失败: {exc}")
    b.log("设置已更新：" + ", ".join(f"{k}={v}" for k, v in applied.items()))
    # 窗口数变化要**立即生效**：调大下次 acquire 时懒开窗，调小关空闲窗口
    if "agent.copilot_windows" in applied:
        try:
            await b.WINDOWS.set_max(int(applied["agent.copilot_windows"]))
            b.log(f"Copilot 自动化窗口数已调整为 {b.WINDOWS.max_windows}")
        except Exception as exc:
            b.log(f"调整 Copilot 窗口数失败（忽略）：{exc}")
    # 子代理并发数变化：提供商逻辑槽要跟着调（否则 8 个扇出只有 4 个能跑，其余等超时）
    if "agent.subagent_parallel" in applied:
        try:
            await b.SLOTS.set_max(max(4, int(applied["agent.subagent_parallel"])))
        except Exception as exc:
            b.log(f"调整提供商并发槽失败（忽略）：{exc}")
    return {"applied": applied, "persisted": persisted}


# raw 编辑器里的敏感值占位：GET 打码返回，POST 遇到占位符保留原值不写回，
# 避免"读出来是 ***、保存时把真密钥覆盖成 ***"。
_REDACTED = "***"

_SENSITIVE_KEY_PARTS = ("api_token", "api_key", "apikey", "secret", "password",
                        "token", "passwd")


def _is_sensitive_key(key: str) -> bool:
    k = str(key or "").lower()
    return any(p in k for p in _SENSITIVE_KEY_PARTS)


def _redact_config(node):
    """递归把敏感键的值换成占位符（键路径含 api_token/api_key/secret/password 等）。"""
    if isinstance(node, dict):
        return {k: (_REDACTED if _is_sensitive_key(k) and isinstance(v, (str, int))
                    else _redact_config(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_redact_config(x) for x in node]
    return node


def _restore_redacted(new: dict, old: dict) -> None:
    """把 new 里仍是占位符的敏感值还原为 old 的原值（原地修改 new）。

    只对敏感键生效：用户真把 api_token 改成别的值时正常写入；
    非敏感键即使值恰好是 *** 也不动（避免误还原）。
    """
    for k, v in list(new.items()):
        if _is_sensitive_key(k) and v == _REDACTED and k in old:
            new[k] = old[k]
        elif isinstance(v, dict) and isinstance(old.get(k), dict):
            _restore_redacted(v, old[k])


@router.get("/v1/config/raw")
async def get_config_raw():
    """返回完整 config.json（原始 JSON），用于高级/raw 编辑。

    敏感键（api_token / *.api_key / secret / password 等）的值打码为 ***——
    默认 loopback 无 token 部署时本机任意进程都能读这个端点，不该零门槛导出明文密钥。
    打码值保存时会被忽略（见 set_config_raw 的占位符保留逻辑）。
    """
    cfg_path = b.BASE / "config.json"
    if not cfg_path.exists():
        return {"config": {}}
    try:
        text = cfg_path.read_text(encoding="utf-8")
        return {"config": _redact_config(json.loads(text))}
    except Exception as exc:
        raise HTTPException(500, f"读取 config.json 失败: {exc}") from exc


@router.post("/v1/config/raw")
async def set_config_raw(payload: dict | None = None):
    """直接写回完整 config.json（高级/raw 编辑）。谨慎使用：写错会导致服务无法启动。"""
    data = payload or {}
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        raise HTTPException(400, "需要 {config: {...}} 结构")
    cfg_path = b.BASE / "config.json"
    # 占位符保留：raw 编辑器读到的敏感值是 ***，保存时不能把真密钥覆盖掉。
    try:
        if cfg_path.exists():
            old = json.loads(cfg_path.read_text(encoding="utf-8") or "{}")
            _restore_redacted(cfg, old)
    except Exception as exc:                 # noqa: BLE001
        b.log(f"raw 保存：读取旧配置做占位符还原失败（按提交内容写）: {exc}")
    try:
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    except Exception as exc:
        raise HTTPException(500, f"写 config.json 失败: {exc}") from exc
    # 同时刷新内存中的 CFG，让多数改动立即生效（部分仍要重启）——同样还原占位符，
    # 否则内存里的密钥被刷成 ***，当前会话的 API 调用会 401。
    try:
        b.CFG.clear()
        b.CFG.update(cfg)
    except Exception as exc:
        b.log(f"刷新内存配置失败（重启后生效）: {exc}")
    b.log("config.json 已通过 raw 编辑器更新")
    return {"ok": True, "path": str(cfg_path)}


@router.get("/v1/plugins/registry")
async def plugin_registry():
    """插件注册表：已安装版本/来源/启停状态 + 备份列表。"""
    return b.PLUGINS.registry_view()


@router.post("/v1/plugins/install")
async def plugin_install(payload: dict | None = None):
    """安装插件：{source, name?, force?}。source 可为目录 / .zip / git URL。"""
    if not bool(getattr(b, "ALLOW_PLUGIN_INSTALL", False)
                or b.CFG.get("allow_plugin_install")):
        raise HTTPException(403, "插件安装默认关闭：在 config.json 设置 allow_plugin_install=true")
    data = payload or {}
    try:
        return b.PLUGINS.install(str(data.get("source") or ""),
                                 str(data.get("name") or ""),
                                 bool(data.get("force")))
    except b.PluginError as exc:
        raise HTTPException(400, str(exc))


@router.post("/v1/plugins/uninstall")
async def plugin_uninstall(payload: dict | None = None):
    """卸载插件：{name, keep_backup?}。"""
    if not bool(getattr(b, "ALLOW_PLUGIN_INSTALL", False)
                or b.CFG.get("allow_plugin_install")):
        raise HTTPException(403, "插件卸载默认关闭：在 config.json 设置 allow_plugin_install=true")
    data = payload or {}
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "需要 name")
    try:
        return b.PLUGINS.uninstall(name, bool(data.get("keep_backup", True)),
                                   allow_builtin=bool(data.get("allow_builtin", False)))
    except b.PluginError as exc:
        raise HTTPException(400, str(exc))


@router.get("/v1/plugins/i18n")
async def plugin_i18n():
    """已加载插件自带的译文（前端合并进字典，让 tr() 覆盖插件文案）。"""
    return b.PLUGINS.i18n()


@router.get("/v1/plugins/panels")
async def plugin_panels():
    """已加载插件声明的声明式面板（前端按 view 渲染）。"""
    return {"panels": b.PLUGINS.panels()}


@router.get("/v1/plugins")
async def list_plugins():
    """插件列表：名称/版本/状态/权限/贡献点（与 DSH 插件语义兼容）。"""
    return {"plugins": b.PLUGINS.list()}


@router.post("/v1/plugins/{name}/{action}")
async def plugin_action(name: str, action: str):
    """插件启停/重载：action = enable | disable | reload。"""
    try:
        if action == "enable":
            return {"ok": True, "plugin": b.PLUGINS.enable(name)}
        if action == "disable":
            return {"ok": True, "plugin": b.PLUGINS.disable(name)}
        if action == "reload":
            return {"ok": True, "plugin": b.PLUGINS.reload(name)}
    except b.PluginError as exc:
        raise HTTPException(400, str(exc))
    raise HTTPException(404, f"未知动作: {action}")


@router.api_route("/v1/plugins/{plugin}/api/{path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def plugin_api(plugin: str, path: str, request: HttpRequest):
    """插件自定义接口的统一分发（启停不需要重启服务）。"""
    try:
        return await b.PLUGINS.dispatch_api(plugin, request.method, path, request)
    except b.PluginError as exc:
        raise HTTPException(404, str(exc))


@router.get("/v1/agent/debug/snapshot")
async def agent_debug_snapshot():
    """诊断：当前自动化页面截图 + 下载入口（只读，排障用）。"""
    return await b.debug_page_snapshot()


@router.get("/v1/agent/browser")
async def browser_state():
    return {"headless": b.HEADLESS_CURRENT,
            "config_headless": bool(b.CFG.get("headless", False))}


@router.post("/v1/agent/browser")
async def set_browser_mode(payload: dict):
    """切换自动化浏览器窗口的显示/隐藏。

    headless 是启动参数，切换必然重建浏览器上下文（EdgeProfile 保留，
    登录态不受影响）。有请求/任务正在使用浏览器时返回 409 拒绝切换。
    persist=true 可把新模式写回 config.json。

    注意：这里**不能**先等 ensure_browser（它最多等 240 秒 Copilot 就绪）。
    启动按钮只需要把窗口拉起来；登录/SSO 由后台 boot_browser 继续处理。
    """
    target_raw = payload.get("headless")
    if target_raw is None:
        # 只启动、不切换模式（"启动自动化浏览器"按钮就是这么调的）
        page = b.current_page()
        if page is not None:
            # 页面可能已经开着但窗口被最小化/压在别的窗口后面；仅返回
            # changed=false 用户会以为按钮没生效。这里把窗口带到前台。
            try:
                await page.bring_to_front()
                focused = True
            except Exception as exc:             # noqa: BLE001
                focused = False
                return {"headless": b.HEADLESS_CURRENT, "changed": False,
                        "fallback": False, "focused": False,
                        "focus_error": str(exc)[:160]}
            return {"headless": b.HEADLESS_CURRENT, "changed": False,
                    "fallback": False, "focused": focused}
        # 只启动：后台跑 boot_browser（含 Copilot 登录等待），HTTP 立即返回。
        # 这样托盘/WebUI 不会卡 240 秒，状态由 /health 的 browser_state 轮询。
        old = a.BOOT.get("task")
        if old is None or old.done():
            a.BOOT["task"] = asyncio.create_task(a.boot_browser())
        return {"headless": b.HEADLESS_CURRENT, "changed": False,
                "fallback": False, "started": True,
                "state": a.BOOT.get("browser_state") or "starting"}
    if not isinstance(target_raw, bool):
        raise HTTPException(422, "headless 必须是布尔值")
    target = target_raw
    if target == b.HEADLESS_CURRENT and b.current_page() is not None:
        return {"headless": b.HEADLESS_CURRENT, "changed": False, "fallback": False}
    if b.any_window_busy():
        raise HTTPException(409, "有请求/任务正在使用浏览器，请稍后再切换")
    actual = await b._start_browser(target)
    result = {"headless": actual, "changed": True, "fallback": actual != target}
    if payload.get("persist"):
        try:
            b.CFG["headless"] = actual
            (b.BASE / "config.json").write_text(
                json.dumps(b.CFG, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            result["persisted"] = True
        except Exception as exc:
            result["persisted"] = False
            result["persist_error"] = str(exc)
    return result


@router.post("/v1/agent/pick-directory")
async def pick_directory(payload: dict | None = None):
    """弹系统文件夹对话框（DSH native 后端的等价实现）；失败时前端回退到 browse。"""
    start = str((payload or {}).get("start") or "")
    try:
        return await b.pick_directory_native(start)
    except b.ToolError as exc:
        raise HTTPException(400, str(exc))


@router.post("/v1/agent/browse/mkdir")
async def browse_mkdir(payload: dict | None = None):
    """在工作目录选择器里新建子目录：{parent, name} → {path}。"""
    data = payload or {}
    try:
        return b.create_directory(str(data.get("parent") or ""), str(data.get("name") or ""))
    except b.ToolError as exc:
        raise HTTPException(400, str(exc))


@router.get("/v1/agent/browse")
async def browse_dirs(path: str | None = None):
    """列目录（只列子目录），供工作目录选择器；不传 path 从当前工作区开始。"""
    return b.browse_directories(path)


@router.get("/v1/agent/workspace")
async def workspace():
    return {"workspace": str(b.AGENT_TOOLS.root)}


@router.post("/v1/agent/workspace")
async def set_workspace(payload: dict):
    if b.any_task_busy():
        raise HTTPException(409, "有任务正在运行，不能切换工作区")
    new_root = b.AGENT_TOOLS.set_root(str(payload.get("path", "")), bool(payload.get("create", False)))
    b.DEFAULT_WORKSPACE = new_root  # 作为之后新任务的默认工作区
    return {"workspace": new_root}


@router.post("/v1/agent/browser/retry")
async def browser_retry():
    """重新初始化浏览器（登录完成后、或上次启动失败时用），不必重启服务。"""
    focused = False
    page = b.current_page()
    if page is not None:
        # 浏览器其实已经起来，只是窗口被最小化/被别的窗口盖住；先把窗口带到前台，
        # 否则用户点"重试浏览器"看不出任何变化。
        try:
            await page.bring_to_front()
            focused = True
        except Exception:                    # noqa: BLE001
            pass
    old = a.BOOT.get("task")
    if old and not old.done():
        old.cancel()
    task = asyncio.create_task(a.boot_browser())
    a.BOOT["task"] = task
    return {"started": True, "state": a.BOOT["browser_state"], "focused": focused}


@router.get("/v1/models/local/status")
async def local_models_status():
    """本地 GGUF 模型的运行状态（是否已加载到内存、加载耗时、推理统计）。"""
    out = []
    for pname, p in (b.PROVIDERS or {}).items():
        if not str(getattr(p, "backend", "")).lower() == "local":
            continue
        engine = getattr(p, "engine", None)
        if engine is None:
            continue
        try:
            stats = engine.stats()
        except Exception as exc:             # noqa: BLE001
            stats = {"error": str(exc)[:120]}
        stats["provider"] = pname
        stats["model_path"] = getattr(p, "model_path", "")
        # 模型文件名（不含路径）方便前端展示
        mp = str(stats.get("model_path") or "")
        stats["model_file"] = Path(mp).name if mp else ""
        out.append(stats)
    return {"providers": out}


@router.post("/v1/models/local/warm")
async def warm_local_models():
    """预热所有 local 提供商（把 GGUF 加载进内存，下次调用不用等）。"""
    results = []
    for pname, p in (b.PROVIDERS or {}).items():
        if not str(getattr(p, "backend", "")).lower() == "local":
            continue
        engine = getattr(p, "engine", None)
        if engine is None:
            continue
        try:
            res = await asyncio.to_thread(engine.warm)
            res["provider"] = pname
            results.append(res)
        except Exception as exc:             # noqa: BLE001
            results.append({"provider": pname, "ok": False, "error": str(exc)[:200]})
    return {"results": results}


def _local_engine(provider: str):
    p = (b.PROVIDERS or {}).get(str(provider or ""))
    if p is None or str(getattr(p, "backend", "")).lower() != "local":
        raise HTTPException(404, f"本地提供商不存在: {provider or '(空)'}")
    engine = getattr(p, "engine", None)
    if engine is None:
        raise HTTPException(404, f"本地提供商没有引擎: {provider}")
    return p, engine


@router.post("/v1/models/local/{provider}/start")
async def start_local_model(provider: str):
    """启动/加载单个本地模型（把 GGUF 载入内存）。

    启动前会体检：路径失效时按文件名在模型库里找同名文件并自动改正配置
    （模型被「整理」移进 chat/embedding/ 后，旧路径就失效了）；文件截断 /
    不是对话模型时给出可行动的错误，而不是让 llama.cpp 抛底层异常。
    """
    p, engine = _local_engine(provider)
    mp = str(getattr(p, "model_path", "") or "")
    repaired: dict | None = None
    if not mp or not Path(mp).is_file():
        want = Path(mp).name if mp else ""
        cands = model_store.find_file(_models_root(), want,
                                      _external_model_dirs()) if want else []
        cands.sort(key=lambda c: (c["kind"] != "chat", -int(c["size"] or 0)))
        best = next((c for c in cands if Path(c["full"]).is_file()), None)
        if best:
            try:
                _set_provider_model_path(provider, best["full"], {"kind": best["kind"]})
                _reload_providers()
                p, engine = _local_engine(provider)
                repaired = {"from": mp, "to": best["full"],
                            "kind": model_store.KIND_LABEL.get(best["kind"], best["kind"])}
                b.log(f"本地模型路径自愈：{provider} {mp} → {best['full']}")
            except HTTPException:
                raise
            except Exception as exc:             # noqa: BLE001
                b.log(f"本地模型路径自愈失败（继续按原路径尝试）：{exc}")
        else:
            where = str(_models_root())
            raise HTTPException(
                404, f"模型文件不存在：{mp or '(未配置 model_path)'}"
                     f"（已在 {where} 及 provider 所在目录查找"
                     + (f"同名文件 {want}" if want else "") + "，未找到；"
                     "请重新下载或在模型卡片上点「设为当前」）")
    det = model_store.detect_kind(p.model_path if hasattr(p, "model_path") else mp)
    if det["kind"] != "chat":
        raise HTTPException(
            400, f"{Path(p.model_path).name} 判定为"
                 f"{model_store.KIND_LABEL.get(det['kind'], det['kind'])}模型（{det['reason']}），"
                 "不能作为对话模型加载；向量模型请在知识库设置里选择")
    if (det.get("header") or {}).get("truncated"):
        raise HTTPException(400, f"{Path(p.model_path).name} 数据不完整（张量区间超出文件大小），"
                                 "请重新下载")
    try:
        res = await asyncio.to_thread(engine.warm)
        return {"provider": provider, "model_path": getattr(p, "model_path", ""),
                **res, "repaired": repaired, "kind": det["kind"]}
    except Exception as exc:                 # noqa: BLE001
        return {"provider": provider, "ok": False, "error": str(exc)[:200],
                "loaded": bool(getattr(engine, "loaded", False)),
                "repaired": repaired}


@router.post("/v1/models/local/{provider}/stop")
async def stop_local_model(provider: str, payload: dict | None = None):
    """停止/卸载单个本地模型（释放内存）。

    `force: true` 是"强行停止"：取消正在跑的生成、放弃正在做的加载、直接释放上下文。
    普通停止会先协作式取消（生成循环下一个 chunk 退出）再卸载；卸载拿不到锁时超时返回，
    不会把 HTTP 请求挂死。
    """
    p, engine = _local_engine(provider)
    payload = payload or {}
    force = bool(payload.get("force"))
    timeout = float(payload.get("timeout") or 5.0)
    try:
        if force and hasattr(engine, "force_stop"):
            res = await asyncio.to_thread(engine.force_stop, timeout)
            ok = bool(res.get("stopped") or res.get("unloaded"))
            return {"provider": provider, "ok": ok, "force": True,
                    "error": "" if ok else "底层调用仍在进行（llama.cpp 不响应中断），"
                                           "已请求取消，本次返回后会释放内存",
                    "model_path": getattr(p, "model_path", ""),
                    "stopped": res, "stats": engine.stats() if hasattr(engine, "stats") else {}}
        if hasattr(engine, "cancel"):
            engine.cancel()                  # 先协作式取消正在跑的生成
        try:
            await asyncio.wait_for(asyncio.to_thread(engine.unload), timeout=max(1.0, timeout))
            ok, error = True, ""
        except asyncio.TimeoutError:
            # 还在忙（加载中/生成不退）：告诉调用方用 force
            ok, error = False, "模型正忙，卸载超时；可加 force:true 强行停止"
        return {"provider": provider, "ok": ok, "force": False, "error": error,
                "model_path": getattr(p, "model_path", ""),
                "stats": engine.stats() if hasattr(engine, "stats") else {}}
    except Exception as exc:                 # noqa: BLE001
        return {"provider": provider, "ok": False, "force": force,
                "error": str(exc)[:200]}


@router.post("/v1/models/local/stop")
async def stop_all_local_models(payload: dict | None = None):
    """一键强行停止所有本地模型（释放内存/卡住的加载）。"""
    payload = payload or {}
    force = payload.get("force", True) is not False
    timeout = float(payload.get("timeout") or 5.0)
    out = []
    for pname, p in list((b.PROVIDERS or {}).items()):
        if str(getattr(p, "backend", "")).lower() != "local":
            continue
        eng = getattr(p, "engine", None)
        if eng is None:
            continue
        try:
            if force and hasattr(eng, "force_stop"):
                res = await asyncio.to_thread(eng.force_stop, timeout)
                if not (res.get("stopped") or res.get("unloaded")):
                    raise RuntimeError("底层调用仍在进行（不可中断），已请求取消")
            else:
                if hasattr(eng, "cancel"):
                    eng.cancel()
                await asyncio.wait_for(asyncio.to_thread(eng.unload), timeout=max(1.0, timeout))
                res = {"stopped": True}
            out.append({"provider": pname, "ok": True, "result": res})
        except Exception as exc:             # noqa: BLE001
            out.append({"provider": pname, "ok": False, "error": str(exc)[:160]})
    return {"ok": all(x["ok"] for x in out), "providers": out,
            "stopped": sum(1 for x in out if x["ok"])}


@router.get("/v1/models/local")
async def list_local_models():
    """列出模型库里的本地 GGUF：类别、可用性、规范位置（放错位置会标出来）。"""
    root = _models_root()
    if not root.exists():
        return {"models": [], "root": str(root)}
    try:
        report = model_store.scan(root, extra_roots=_external_model_dirs())
    except Exception as exc:                 # noqa: BLE001
        b.log(f"扫描模型库失败：{exc}")
        return {"models": [], "root": str(root),
                "error": f"扫描失败：{str(exc)[:160]}"}
    out = []
    for m in report["models"]:
        out.append({
            "name": m["name"], "path": m["path"], "full": m["full"],
            "size": m["size"], "mtime": m["mtime"],
            # 新字段：类别与体检结果（界面据此给图标/警告/整理建议）
            "kind": m["kind"], "kind_label": m["kind_label"],
            "kind_source": m["source"], "kind_reason": m["reason"],
            "arch": m["arch"], "valid": m["valid"], "issues": m["issues"],
            "canonical": m["canonical"], "needs_move": m["needs_move"],
            "inside": m["inside"],
        })
    return {"models": out, "root": str(root),
            "needs_organize": report["needs_organize"], "broken": report["broken"]}


@router.post("/v1/models/local/scan")
async def scan_local_models(payload: dict | None = None):
    """整理模型库：识别类别并把放错位置的文件移进规范子目录。

    默认 dry-run（apply=False），只回计划；apply=True 才移动文件。
    目标重名会追加 -1/-2，绝不覆盖。
    """
    payload = payload or {}
    root = _models_root()
    try:
        report = await asyncio.to_thread(model_store.organize, root,
                                         apply=bool(payload.get("apply")),
                                         extra_roots=_external_model_dirs())
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(500, f"整理模型库失败：{str(exc)[:160]}") from exc
    return report


@router.post("/v1/models/local/{provider}/use")
async def use_local_model(provider: str, payload: dict | None = None):
    """把模型库里的某个 GGUF 设为该本地 provider 的当前模型。

    这是"下载完就能用"的入口：以前只能手改 config.json 的 model_path，
    下载到 data/models 的新文件对界面不可见（启动的仍是旧路径）。
    """
    rel = str((payload or {}).get("path") or "").strip()
    root = _models_root()
    try:
        target = model_store.safe_path(root, rel)
    except model_store.ModelStoreError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not target.is_file():
        raise HTTPException(404, f"模型文件不存在：{rel}")
    kind = model_store.detect_kind(target)
    if kind["kind"] != "chat":
        raise HTTPException(
            400, f"{target.name} 是{model_store.KIND_LABEL.get(kind['kind'], kind['kind'])}"
                 f"模型，不能作为对话 provider 的模型（{kind['reason']}）")
    if kind["header"].get("truncated"):
        raise HTTPException(400, f"{target.name} 数据不完整，重新下载后再设为当前模型")
    saved = _set_provider_model_path(provider, str(target), kind)
    _reload_providers()
    return {"provider": provider, "model_path": str(target),
            "name": target.name, "kind": kind["kind"], **saved}



class _ModelDownloadTooLarge(Exception):
    def __init__(self, limit_mb: int):
        super().__init__(f"下载超过上限 {limit_mb} MB，已中止并删除半成品")
        self.limit_mb = limit_mb


def _download_model_sync(url: str, dest: Path, max_bytes: int) -> int:
    """在 worker 线程里流式下载；写 dest 只在单线程内发生，失败由调用方清理。"""
    req = urllib.request.Request(url, headers={"User-Agent": "gantry-local-model/1.0"})
    limit_mb = max(1, max_bytes // (1024 * 1024))
    with urllib.request.urlopen(req, timeout=3600) as resp, open(dest, "wb") as out:
        cl = resp.headers.get("Content-Length")
        if cl and cl.isdigit() and int(cl) > max_bytes:
            raise _ModelDownloadTooLarge(limit_mb)
        total = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise _ModelDownloadTooLarge(limit_mb)
            out.write(chunk)
    return total


@router.post("/v1/models/local/download")
async def download_local_model(payload: dict):
    """从 URL 下载模型到模型库，并按类别放进 chat/embedding/rerank/mmproj。

    下载完立刻体检：不是 GGUF / 数据截断 → 删除半成品并报错（否则文件留在库里，
    「启动」时才发现加载失败，很难判断是下载问题还是路径问题）。
    """
    url = str(payload.get("url") or "")
    if not url:
        raise HTTPException(400, "缺少 url")
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise HTTPException(400, f"仅支持 http/https 下载: {scheme or '缺少 scheme'}")
    filename = str(payload.get("filename") or "").strip()
    if not filename:
        # 从 URL 路径取文件名，并确保 .gguf 后缀
        filename = Path(urllib.parse.urlparse(url).path).name or "model.gguf"
    if not filename.lower().endswith(".gguf"):
        filename += ".gguf"
    # 防止目录遍历：只取 basename
    filename = Path(filename).name
    root = _models_root()
    # 先落在库根目录，体检通过后再按类别归位（失败好清理）
    tmp_dest = root / filename
    if tmp_dest.exists():
        raise HTTPException(409, f"文件已存在: {filename}")
    max_mb = max(1, int(b.CFG.get("max_model_download_mb") or 20 * 1024))
    max_bytes = max_mb * 1024 * 1024
    try:
        tmp_dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_download_model_sync, url, tmp_dest, max_bytes)
    except _ModelDownloadTooLarge as exc:
        # 超限中止：删除半成品
        try:
            tmp_dest.unlink(missing_ok=True)
        except Exception:                # noqa: BLE001
            pass
        raise HTTPException(400, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        # 失败时清理半成品
        try:
            tmp_dest.unlink(missing_ok=True)
        except Exception:                # noqa: BLE001
            pass
        raise HTTPException(500, f"下载失败: {exc}") from exc

    # ── 体检 + 归位 ──
    hint = str(payload.get("kind") or "").strip().lower()
    det = model_store.detect_kind(tmp_dest)
    hdr = det.get("header") or {}
    if hdr.get("error"):
        try:
            tmp_dest.unlink(missing_ok=True)
        except Exception:                # noqa: BLE001
            pass
        raise HTTPException(400, f"下载的文件不是有效 GGUF：{hdr['error']}")
    if hdr.get("truncated"):
        try:
            tmp_dest.unlink(missing_ok=True)
        except Exception:                # noqa: BLE001
            pass
        raise HTTPException(
            400, f"下载的文件数据不完整（{filename}）：张量区间超出文件大小，"
                 "常见于连接中断/被网关截断，请重新下载")
    kind = hint if hint in model_store.KINDS else det["kind"]
    dest = root / kind / filename
    n = 1
    while dest.exists():
        dest = root / kind / f"{Path(filename).stem}-{n}{Path(filename).suffix}"
        n += 1
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_dest, dest)
    except OSError:
        import shutil
        shutil.move(str(tmp_dest), str(dest))
    return {"ok": True, "name": dest.name, "kind": kind,
            "kind_label": model_store.KIND_LABEL.get(kind, kind),
            "kind_source": "hint" if hint in model_store.KINDS else det["source"],
            "kind_reason": det["reason"],
            "path": str(dest.relative_to(root)).replace("\\", "/"),
            "full": str(dest), "size": dest.stat().st_size}


@router.delete("/v1/models/local/{filename:path}")
async def delete_local_model(filename: str):
    """删除本地模型文件。支持类别子目录（chat/xxx.gguf），路径限制在模型库内。"""
    try:
        return await asyncio.to_thread(model_store.delete, _models_root(), filename)
    except model_store.ModelStoreError as exc:
        msg = str(exc)
        raise HTTPException(404 if "不存在" in msg else 400, msg) from exc
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(500, f"删除失败: {exc}") from exc
