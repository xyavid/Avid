"""``GET /api/runs/{run_id}/events``：SSE 事件流。

游标来源优先级：``?after=`` > ``Last-Event-ID`` header > 0。两者语义相同
（durable ``seq``），显式参数留给前端在重连时自己算，header 留给浏览器自动重发。
未知 run → JSON 404，不回落 SPA（不变量 B10）。
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse

from ...svc import STREAM_HEARTBEAT_SECONDS, Services, TooManyStreams
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


def _leased(services: Services, body: Iterator[str]) -> Iterator[str]:
    """把并发额度包在生成器外侧。

    放在生成器里（而不是路由的 try/finally）：`finally` 在正常结束、客户端断连、
    迭代器被关闭三种情况下都会跑；路由那层只覆盖"返回响应之前就失败"。
    """
    try:
        yield from body
    finally:
        services.streams.release()


@router.get("/runs/{run_id}/events")
def stream_events(
    request: Request,
    run_id: str,
    after: int | None = Query(default=None, ge=0),
    deltas: int = Query(default=0, ge=0, le=1),
) -> StreamingResponse:
    """一个 run 至多一条事件流；delta 需显式订阅（``?deltas=1``）。

    并发额度用尽就回 503：Starlette 对**同步**生成器用 `iterate_in_threadpool`，
    而生成器的 `next()` 会阻塞到下一个事件或心跳——一条连接因此长期占住 anyio
    默认线程池（40）里的一个线程。与其让 REST 被悄悄饿死，不如显式拒绝（见
    `svc.MAX_CONCURRENT_STREAMS` 的说明）。
    """
    services = current_services(request)
    record = services.runs.get(run_id)
    cursor = _cursor(request, after)
    if not services.streams.acquire():
        raise TooManyStreams(
            f"同时打开的事件流太多（上限 {services.streams.limit}），稍后重试"
        )
    body = _leased(
        services,
        sse.stream(
            services.runs,
            record,
            after=cursor,
            deltas=bool(deltas),
            # 心跳是传输层常量：以前为了拿它调 services.meta()（连带扫一遍技能目录），
            # 于是"建立一条事件流"变成一次磁盘 IO。
            heartbeat=STREAM_HEARTBEAT_SECONDS,
        ),
    )
    return StreamingResponse(
        body,
        status_code=status.HTTP_200_OK,
        media_type="text/event-stream",
        headers=dict(sse.STREAM_HEADERS),
    )


__all__ = ["router"]
