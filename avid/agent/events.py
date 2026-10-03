"""Event name registry: the single source for event names, tiers and stream timing constants."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

# Durable events: replayable, carry id and seq, and define reconnect catch-up granularity.
RUN_STARTED = "run_started"
USER_MESSAGE = "user_message"
ASSISTANT_MESSAGE = "assistant_message"
TOOL_RESULT_MESSAGE = "tool_result_message"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_FINISHED = "tool_call_finished"
TOOL_CALL_DENIED = "tool_call_denied"
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_RESOLVED = "approval_resolved"
CONTEXT_COMPACTED = "context_compacted"
TODO_REMINDER = "todo_reminder"
STOP_NUDGE = "stop_nudge"
RUN_FINISHED = "run_finished"
RUN_FAILED = "run_failed"
RUN_CANCELLED = "run_cancelled"
RESYNC = "resync"

# Transient events: state only, dropped on disconnect and never replayed.
RUN_STATUS = "run_status"

# Delta events: lossy fragments, delivered only when a subscriber opts in.
ASSISTANT_DELTA = "assistant_delta"
#: Reasoning-token delta: rendered in its own card and never merged into the reply body.
REASONING_DELTA = "reasoning_delta"


DURABLE_EVENT_TYPES: tuple[str, ...] = (
    RUN_STARTED,
    USER_MESSAGE,
    ASSISTANT_MESSAGE,
    TOOL_RESULT_MESSAGE,
    TOOL_CALL_STARTED,
    TOOL_CALL_FINISHED,
    TOOL_CALL_DENIED,
    APPROVAL_REQUESTED,
    APPROVAL_RESOLVED,
    CONTEXT_COMPACTED,
    TODO_REMINDER,
    STOP_NUDGE,
    RUN_FINISHED,
    RUN_FAILED,
    RUN_CANCELLED,
    RESYNC,
)

TRANSIENT_EVENT_TYPES: tuple[str, ...] = (RUN_STATUS,)

DELTA_EVENT_TYPES: tuple[str, ...] = (ASSISTANT_DELTA, REASONING_DELTA)

#: The only hand-written event-name list; the union below derives from it, so the two cannot drift.
EventType = Literal[
    "run_started",
    "user_message",
    "assistant_message",
    "tool_result_message",
    "tool_call_started",
    "tool_call_finished",
    "tool_call_denied",
    "approval_requested",
    "approval_resolved",
    "context_compacted",
    "todo_reminder",
    "stop_nudge",
    "run_finished",
    "run_failed",
    "run_cancelled",
    "resync",
    "run_status",
    "assistant_delta",
    "reasoning_delta",
]

#: Full event list in declaration order, which the frontend type union is compared against.
EVENT_TYPES: tuple[EventType, ...] = get_args(EventType)

#: Terminal events: the frontend cancels pending deltas when one arrives.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {RUN_FINISHED, RUN_FAILED, RUN_CANCELLED}
)

DURABLE_SET: frozenset[str] = frozenset(DURABLE_EVENT_TYPES)

# Client-visible timing: the published heartbeat, the generator's heartbeat and the frontend
# timeout must all agree, otherwise the client and server disagree about a dead connection.

#: SSE heartbeat interval; after this silence the server sends a comment frame as a ping.
STREAM_HEARTBEAT_SECONDS = 15.0
#: Silence after which the client reconciles with the run registry instead of trusting the stream.
TERMINAL_FALLBACK_SECONDS = 30.0


def now_ms() -> int:
    """Event timestamp in milliseconds from the server clock; remote wall clocks never compare."""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class RunEvent:
    """One run event; a seq of None means the event is outside the replay buffer."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    seq: int | None = None
    ts: int = 0

    @property
    def durable(self) -> bool:
        """Whether this event can be replayed: it needs both a seq and a durable type."""
        return self.seq is not None and self.type in DURABLE_SET


def event(type: str, **data: Any) -> RunEvent:
    """Build a kernel-side event; run_id and seq stay empty for the svc registry to fill in."""
    if type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {type!r}；可用：{'、'.join(EVENT_TYPES)}")
    return RunEvent(type=type, data=data, ts=now_ms())


class RunObserver(Protocol):
    """Event observer; the svc implementation assigns seq, buffers and wakes subscribers."""

    def __call__(self, event: RunEvent) -> None: ...


__all__ = [
    "ASSISTANT_DELTA",
    "ASSISTANT_MESSAGE",
    "APPROVAL_REQUESTED",
    "APPROVAL_RESOLVED",
    "CONTEXT_COMPACTED",
    "DELTA_EVENT_TYPES",
    "DURABLE_EVENT_TYPES",
    "DURABLE_SET",
    "EVENT_TYPES",
    "EventType",
    "REASONING_DELTA",
    "RESYNC",
    "RUN_CANCELLED",
    "RUN_FAILED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "RUN_STATUS",
    "STOP_NUDGE",
    "TERMINAL_EVENT_TYPES",
    "TODO_REMINDER",
    "TOOL_CALL_DENIED",
    "TOOL_CALL_FINISHED",
    "TOOL_CALL_STARTED",
    "TOOL_RESULT_MESSAGE",
    "TRANSIENT_EVENT_TYPES",
    "USER_MESSAGE",
    "RunEvent",
    "RunObserver",
    "event",
    "now_ms",
]
