# -*- coding: utf-8 -*-
"""自身更新 API：检查 / 安装 / 回滚。"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from . import updater as u

router = APIRouter()


def _updater() -> u.Updater:
    try:
        return u.from_config(config=None)
    except Exception as exc:                     # noqa: BLE001
        raise HTTPException(400, f"更新配置错误: {exc}") from exc


@router.get("/v1/update/status")
async def update_status():
    up = _updater()
    return up.status()


@router.get("/v1/update/check")
async def update_check(method: str = ""):
    up = _updater()
    try:
        return await asyncio.to_thread(up.check, method)
    except u.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/v1/update/apply")
async def update_apply(payload: dict[str, Any] | None = None):
    body = payload or {}
    up = _updater()
    if not up.enabled:
        raise HTTPException(403, "更新系统未启用（update.enabled=false）")
    try:
        return await asyncio.to_thread(
            up.apply,
            version=str(body.get("version") or ""),
            method=str(body.get("method") or ""),
            restart=bool(body.get("restart", True)),
            dry_run=bool(body.get("dry_run", False)),
            force=bool(body.get("force", False)),
        )
    except u.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/v1/update/rollback")
async def update_rollback(payload: dict[str, Any] | None = None):
    body = payload or {}
    up = _updater()
    try:
        out = await asyncio.to_thread(up.rollback, str(body.get("backup") or ""))
    except u.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc
    if bool(body.get("restart", True)):
        from . import lifecycle
        lifecycle.schedule("restart", 1.2)
        out["restart_required"] = True
    return out
