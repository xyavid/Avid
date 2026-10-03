"""SSE framing: only durable events carry an ``id:`` field, so a resume stops at the last of them."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any

from ..runtime.events import STREAM_HEARTBEAT_SECONDS
from .schemas import event_payload

logger = logging.getLogger("avid.web.sse")

# Same constant the client reads from the metadata endpoint, so the two timeouts cannot drift apart.
HEARTBEAT_SECONDS = STREAM_HEARTBEAT_SECONDS

# Comment-only frame that keeps idle connections alive through intermediary proxies.
PING = ": ping\n\n"

STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Ask reverse proxies not to buffer, or events would be held back and delivered in one batch.
    "X-Accel-Buffering": "no",
}


def encode(payload: dict[str, Any]) -> str:
    """Encodes one event frame, writing ``id:`` only when the payload carries a durable sequence."""
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
    """Encodes a run's event stream to SSE text on a worker thread, turning the None yield into a ping."""
    for event in registry.subscribe(
        record.run_id, after=after, deltas=deltas, heartbeat=heartbeat
    ):
        if event is None:
            yield PING
            continue
        yield encode(event_payload(event, record.session_id))


async def stream_async(
    registry: Any,
    record: Any,
    *,
    after: int = 0,
    deltas: bool = False,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[str]:
    """Async twin of ``stream`` that waits on the event loop instead of occupying a thread pool slot."""
    async for event in registry.subscribe_async(
        record.run_id, after=after, deltas=deltas, heartbeat=heartbeat
    ):
        if event is None:
            yield PING
            continue
        yield encode(event_payload(event, record.session_id))


__all__ = [
    "HEARTBEAT_SECONDS",
    "PING",
    "STREAM_HEADERS",
    "encode",
    "stream",
    "stream_async",
]
