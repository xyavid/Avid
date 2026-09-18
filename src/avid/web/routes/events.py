"""``GET /api/runs/{run_id}/events``：SSE 事件流。

游标来源优先级：``?after=`` > ``Last-Event-ID`` header > 0。两者语义相同
（durable ``seq``），显式参数留给前端在重连时自己算，header 留给浏览器自动重发。
未知 run → JSON 404，不回落 SPA（不变量 B10）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from .. import sse
from . import current_services

router = APIRouter()


def _cursor(request: Request, after: int | None) -> int:
    if after is not None:
        return max(0, after)
    raw = request.headers.get("last-event-id", "").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@router.get("/runs/{run_id}/events")
def stream_events(
    request: Request,
    run_id: str,
    after: int | None = Query(default=None, ge=0),
    deltas: int = Query(default=0, ge=0, le=1),
) -> StreamingResponse:
    """一个 run 至多一条事件流；delta 需显式订阅（``?deltas=1``）。"""
    services = current_services(request)
    record = services.runs.get(run_id)
    cursor = _cursor(request, after)
    body = sse.stream(
        services.runs,
        record,
        after=cursor,
        deltas=bool(deltas),
        heartbeat=services.meta()["stream"]["heartbeat_seconds"],
    )
    return StreamingResponse(
        body,
        status_code=status.HTTP_200_OK,
        media_type="text/event-stream",
        headers=dict(sse.STREAM_HEADERS),
    )


__all__ = ["router"]
