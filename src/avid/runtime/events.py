"""事件类型单点定义（F0）。

这个模块是**唯一**知道事件名字符串的地方：内核的观察点、``svc/`` 的注册表、
``web/`` 的编帧都用这里的常量，``tests/test_event_contract.py`` 拿
``EVENT_TYPES`` 去比对前端的联合类型成员集合（不变量契约见设计文档 §6.3/A7）。

三条分档规则（设计文档 §5.2），由 ``DURABLE_EVENT_TYPES`` 一个集合表达：

* **durable**：带 ``id``/``seq``，可重放——重连补齐的粒度就是它；
* **transient**：不带 ``id``，状态类，断了就断了；
* **delta**：不带 ``id``，可任意丢；默认不投递，消费方用 ``?deltas=1`` 显式订阅。

``RunEvent`` 是**普通 dataclass**，不是 pydantic：内核类型保持不可知传输层
（设计文档 §4.2）。``run_id``/``seq``/``ts`` 由 ``svc/runs.py`` 的唯一发射
线程填写，所以内核侧构造时它们可以是空/None。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, get_args

# ---- 事件名：durable ----

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

# ---- 事件名：transient ----

RUN_STATUS = "run_status"

# ---- 事件名：delta ----

ASSISTANT_DELTA = "assistant_delta"


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

DELTA_EVENT_TYPES: tuple[str, ...] = (ASSISTANT_DELTA,)

#: 事件名的**唯一手写清单**。`EVENT_TYPES` 由它派生（`get_args`），所以"联合类型
#: 里有一个、清单里没有"这种分叉不可能出现——以前是两份字面量靠人对齐。
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
]

#: 完整清单（顺序 = `EventType` 的书写顺序）。前端类型联合按它比对。
EVENT_TYPES: tuple[EventType, ...] = get_args(EventType)

#: 终态事件：前端据此 cancel 待处理 delta（不变量 I12）。
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {RUN_FINISHED, RUN_FAILED, RUN_CANCELLED}
)

DURABLE_SET: frozenset[str] = frozenset(DURABLE_EVENT_TYPES)

# ---- 客户端可见的时间常量 ----
#
# 三处各写一份 15.0 / 30.0 的时代结束了：`/api/meta` 公布的
# `stream.heartbeat_seconds`、SSE 生成器的心跳、前端据此设的超时必须是同一个数，
# 否则"客户端等得比心跳久"或"服务端发得比客户端耐心"这类错位只能靠人发现。
# 放在 events.py：它已经是"传输契约"的单点模块（事件名也在这里）。

#: SSE 心跳间隔：这么久没有真事件就发一个注释帧，客户端据此判断连接还活着。
STREAM_HEARTBEAT_SECONDS = 15.0
#: 静默兜底：客户端这么久没收到东西后应与注册表对账（不变量 I13）。
TERMINAL_FALLBACK_SECONDS = 30.0


def now_ms() -> int:
    """事件时间戳（毫秒）。只用服务端时钟——跨机时间不参与游标比较。"""
    return int(time.time() * 1000)


@dataclass(frozen=True)
class RunEvent:
    """一条运行事件。``seq`` 为 None 表示不参与重放。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    seq: int | None = None
    ts: int = 0

    @property
    def durable(self) -> bool:
        return self.seq is not None and self.type in DURABLE_SET


def event(type: str, **data: Any) -> RunEvent:
    """构造一条内核侧事件：run_id/seq 留空，由 svc 的注册表填写。"""
    if type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {type!r}；可用：{'、'.join(EVENT_TYPES)}")
    return RunEvent(type=type, data=data, ts=now_ms())


class RunObserver(Protocol):
    """事件观察者。svc 实现它并负责分配 seq、落缓冲、唤醒订阅者。"""

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
