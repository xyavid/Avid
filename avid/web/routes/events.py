"""SSE endpoint that streams one run's events from an explicit cursor or the last event id."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from ...svc import STREAM_HEARTBEAT_SECONDS, TooManyStreams
from ..sse import STREAM_HEADERS, stream_async
from . import current_services

router = APIRouter()


def _cursor(request: Request, after: int | None) -> int:
    """Picks the resume cursor: the explicit query parameter first, the Last-Event-ID header second."""
    if after is not None:
        return max(0, after)
    raw = request.headers.get("last-event-id", "").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


@router.get("/runs/{run_id}/events")
async def stream_events(
    request: Request,
    run_id: str,
    after: int | None = Query(default=None, ge=0),
    deltas: int = Query(default=0, ge=0, le=1),
) -> StreamingResponse:
    """Streams one run at most once, delivering delta events only when they are explicitly requested."""
    services = current_services(request)
    record = services.runs.get(run_id)
    cursor = _cursor(request, after)
    if not services.streams.acquire():
        raise TooManyStreams(
            f"同时打开的事件流太多（上限 {services.streams.limit}），稍后重试"
        )

    async def body():
        try:
            async for frame in stream_async(
                services.runs,
                record,
                after=cursor,
                deltas=bool(deltas),
                # Passing the constant avoids a metadata call that would scan the skill directory per stream.
                heartbeat=STREAM_HEARTBEAT_SECONDS,
            ):
                yield frame
        finally:
            # The stream slot is released even when the client disconnects mid-flight.
            services.streams.release()

    return StreamingResponse(
        body(),
        status_code=status.HTTP_200_OK,
        media_type="text/event-stream",
        headers=dict(STREAM_HEADERS),
    )


__all__ = ["router"]
