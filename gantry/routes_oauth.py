# -*- coding: utf-8 -*-
"""OAuth 一键授权 API：发起 / 状态 / 取消 / 断开。

token 只落 data/oauth_tokens.json（gantry/oauth.py），本模块不做任何持久化。
设备码流程的 device_code 不出后端——前端只拿到 user_code 和验证链接。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from . import oauth as o

router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent


@router.get("/v1/oauth/providers")
async def oauth_providers():
    """所有支持 OAuth 的厂商及其授权状态（给设置页渲染按钮）。"""
    return {"providers": {name: o.provider_status(_ROOT, name)
                          for name in o.OAUTH_PROVIDERS}}


@router.post("/v1/oauth/{name}/start")
async def oauth_start(name: str):
    spec = o.OAUTH_PROVIDERS.get(name)
    if not spec:
        raise HTTPException(404, f"{name} 不支持 OAuth 授权")
    try:
        if spec["flow"] == "device":
            return await asyncio.to_thread(o.start_device_flow, _ROOT, name)
        return await asyncio.to_thread(o.start_callback_flow, _ROOT, name)
    except Exception as exc:                     # noqa: BLE001
        raise HTTPException(400, f"发起授权失败: {exc}") from exc


@router.get("/v1/oauth/flow/{flow_id}")
async def oauth_flow_status(flow_id: str):
    st = o.flow_status(_ROOT, flow_id)
    if not st:
        raise HTTPException(404, "授权流程不存在或已结束")
    return st


@router.post("/v1/oauth/flow/{flow_id}/cancel")
async def oauth_cancel(flow_id: str):
    if not o.cancel_flow(flow_id):
        raise HTTPException(404, "授权流程不存在或已结束")
    return {"ok": True}


@router.post("/v1/oauth/{name}/disconnect")
async def oauth_disconnect(name: str):
    if name not in o.OAUTH_PROVIDERS:
        raise HTTPException(404, f"{name} 不支持 OAuth 授权")
    removed = await asyncio.to_thread(o.disconnect, _ROOT, name)
    return {"ok": True, "removed": removed}
