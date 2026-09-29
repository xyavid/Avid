"""``GET /api/runs/{run_id}/events``：SSE 事件流。

游标来源优先级：``?after=`` > ``Last-Event-ID`` header > 0。两者语义相同
（durable ``seq``），显式参数留给前端在重连时自己算，header 留给浏览器自动重发。
未知 run → JSON 404，不回落 SPA（不变量 B10）。

阶段 30d 起订阅走 asyncio 事件桥（``subscribe_async``）：连接不再经
``iterate_in_threadpool`` 占线程池线程，等待发生在事件循环里——24 条流的上限
因此放开（见 ``svc.MAX_CONCURRENT_STREAMS``）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from ...svc import STREAM_HEARTBEAT_SECONDS, Services, TooManyStreams
from ..sse import STREAM_HEADERS, stream_async
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
async def stream_events(
    request: Request,
    run_id: str,
    after: int | None = Query(default=None, ge=0),
    deltas: int = Query(default=0, ge=0, le=1),
) -> StreamingResponse:
    """一个 run 至多一条事件流；delta 需显式订阅（``?deltas=1``）。

    并发额度仍保留一个高水位上限（防失控客户端），但 async 生成器的等待发生在
    事件循环里，不再与 REST 抢线程——上限从"线程池余量"变成了纯粹的护栏。
    """
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
                # 心跳是传输层常量：以前为了拿它调 services.meta()（连带扫一遍技能目录），
                # 于是"建立一条事件流"变成一次磁盘 IO。
                heartbeat=STREAM_HEARTBEAT_SECONDS,
            ):
                yield frame
        finally:
            services.streams.release()

    return StreamingResponse(
        body(),
        status_code=status.HTTP_200_OK,
        media_type="text/event-stream",
        headers=dict(STREAM_HEADERS),
    )


__all__ = ["router"]
