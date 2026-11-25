# -*- coding: utf-8 -*-
"""实时视图路由：页面列表 / 打开 URL / SSE 画面流。"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/v1/live/pages")
async def live_pages():
    """受管浏览器页面列表 + 最近一次 open_url（前端据此自动展开面板）。"""
    from . import liveview
    try:
        return await liveview.list_pages()
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(502, str(exc)[:200]) from exc


@router.post("/v1/live/open")
async def live_open(payload: dict | None = None):
    """在受管浏览器新开页访问 URL（只读观看；互动转发是后续版本）。"""
    from . import liveview
    url = str((payload or {}).get("url") or "").strip()
    if not url:
        raise HTTPException(400, "缺少 url")
    try:
        return await liveview.open_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(502, str(exc)[:200]) from exc


@router.post("/v1/live/input")
async def live_input(payload: dict | None = None):
    """互动转发：面板的点击/滚轮/键盘写回受管浏览器页面。"""
    from . import liveview
    body = payload or {}
    index = int(body.get("index") or 0)
    try:
        return await liveview.input_event(index, body)
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:                             # noqa: BLE001
        raise HTTPException(502, str(exc)[:200]) from exc


@router.get("/v1/live/stream")
async def live_stream(index: int = 0):
    """SSE 画面流：data 是 JSON {"img": base64 JPEG, "w","h" 视口 CSS 像素}；
    页面静止时发 ': ping' 心跳。"""
    from . import liveview

    async def _gen():
        try:
            async for frame in liveview.stream_frames(index):
                if frame is None:
                    yield ": ping\n\n"
                else:
                    yield f"data: {json.dumps(frame, separators=(',', ':'))}\n\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:                         # noqa: BLE001
            log.info("liveview 流结束(index=%s): %s", index, exc)
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)[:200]})}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
