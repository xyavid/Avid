"""SSE 编帧：只有 durable 事件写 ``id:``（设计文档 §5.1）。

这条规则本身就是重连语义：浏览器自动重发 ``Last-Event-ID`` 时，它自然停在最后
一个 durable 事件上，delta 与 transient 不参与补齐——不需要任何额外约定。
心跳是与重连独立的机制，不依赖浏览器默认行为。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from .schemas import event_payload

logger = logging.getLogger("avid.web.sse")

# 每 15s 一行注释帧穿透中间代理（比 Flowise 的 30s 更保守）。
HEARTBEAT_SECONDS = 15.0

PING = ": ping\n\n"

STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 让反向代理不缓冲：否则事件会被攒起来一次性送达。
    "X-Accel-Buffering": "no",
}


def encode(payload: dict[str, Any]) -> str:
    """一条事件帧。``seq`` 为 None（transient / delta）时不写 ``id:``。"""
    lines: list[str] = []
    seq = payload.get("seq")
    if seq is not None:
        lines.append(f"id: {seq}")
    lines.append(f"event: {payload.get('type', 'message')}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False, default=str))
    return "\n".join(lines) + "\n\n"


def stream(
    registry: Any,
    record: Any,
    *,
    after: int = 0,
    deltas: bool = False,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> Iterator[str]:
    """把一个 run 的事件流编成 SSE 文本。

    游标补齐、缓冲淘汰时的 ``resync``、以及「delta 默认不投递」都在
    ``svc/runs.py`` 里决定；这里只负责把事件变成字节。
    """
    for event in registry.subscribe(
        record.run_id, after=after, deltas=deltas, heartbeat=heartbeat
    ):
        if event is None:
            yield PING
            continue
        yield encode(event_payload(event, record.session_id))


__all__ = ["HEARTBEAT_SECONDS", "PING", "STREAM_HEADERS", "encode", "stream"]
