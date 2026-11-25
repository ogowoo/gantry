# -*- coding: utf-8 -*-
"""自身更新 API：检查 / 安装 / 回滚。"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import APIRouter, HTTPException

from . import updater as u

router = APIRouter()

# AUD-P1-14：apply/rollback 的互斥必须是**进程级**。Updater 的锁是实例级，而
# HTTP 每个请求新建 Updater——两个标签页同时 apply 会交错替换文件树，得到混合
# 版本（审核 ⑦-6，M20 的并发保护实际无效）。
_UPDATE_MUTEX = threading.Lock()


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
        # 锁必须在 worker 线程里获取：async 路径上持 threading.Lock 跨 await 会让
        # 等待方阻塞事件循环线程，与持锁方互等（与 AUD-P0-02 同一死锁模式）。
        def _apply():
            with _UPDATE_MUTEX:
                return up.apply(
                    version=str(body.get("version") or ""),
                    method=str(body.get("method") or ""),
                    restart=bool(body.get("restart", True)),
                    dry_run=bool(body.get("dry_run", False)),
                    force=bool(body.get("force", False)),
                )

        return await asyncio.to_thread(_apply)
    except u.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/v1/update/rollback")
async def update_rollback(payload: dict[str, Any] | None = None):
    body = payload or {}
    up = _updater()
    try:
        def _rollback():
            with _UPDATE_MUTEX:
                return up.rollback(str(body.get("backup") or ""))

        out = await asyncio.to_thread(_rollback)
    except u.UpdateError as exc:
        raise HTTPException(400, str(exc)) from exc
    if bool(body.get("restart", True)):
        from . import lifecycle
        lifecycle.schedule("restart", 1.2)
        out["restart_required"] = True
    return out
