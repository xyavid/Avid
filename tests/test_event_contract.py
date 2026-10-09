"""Both sides must name the same events: ``EVENT_TYPES`` in ``agent/events.py`` and the union
members inside the EVENTS:BEGIN/END block of ``web/src/events/types.ts``, compared as sets.
"""

from __future__ import annotations

import re
from pathlib import Path

from avid.agent.events import (
    DELTA_EVENT_TYPES,
    DURABLE_EVENT_TYPES,
    EVENT_TYPES,
    TRANSIENT_EVENT_TYPES,
)

ROOT = Path(__file__).resolve().parents[1]
TYPES_TS = ROOT / "web" / "src" / "events" / "types.ts"

_BLOCK = re.compile(r"// EVENTS:BEGIN(.*?)// EVENTS:END", re.S)
_MEMBER = re.compile(r"['\"]([a-z_]+)['\"]")


def frontend_event_types() -> set[str]:
    text = TYPES_TS.read_text(encoding="utf-8")
    match = _BLOCK.search(text)
    assert match is not None, "types.ts 缺少 EVENTS:BEGIN / EVENTS:END 标记块"
    return set(_MEMBER.findall(match.group(1)))


def test_frontend_and_kernel_event_lists_are_equal():
    assert frontend_event_types() == set(EVENT_TYPES)


def test_frontend_declares_the_same_tiers():
    text = TYPES_TS.read_text(encoding="utf-8")
    for pivot in DURABLE_EVENT_TYPES:
        assert f'"{pivot}"' in text or f"'{pivot}'" in text
    assert set(DURABLE_EVENT_TYPES) | set(TRANSIENT_EVENT_TYPES) | set(
        DELTA_EVENT_TYPES
    ) == set(EVENT_TYPES)


def test_event_type_literals_are_unique_and_non_empty():
    assert len(EVENT_TYPES) == len(set(EVENT_TYPES))
    assert all(name.strip() for name in EVENT_TYPES)


def test_event_types_are_derived_from_the_literal():
    """``EVENT_TYPES`` is derived from ``EventType``, the only hand-written list."""
    from typing import get_args

    from avid.agent.events import EventType

    assert get_args(EventType) == EVENT_TYPES
    assert set(EventType.__args__) == set(EVENT_TYPES)


def test_stream_timing_constants_have_exactly_one_definition():
    """One definition for the timing value clients see: ``/api/meta`` publishes
    ``stream.heartbeat_seconds``, and the SSE heartbeat and the registry default must be it.
    """
    import inspect

    from avid import services as svc
    from avid.agent.events import STREAM_HEARTBEAT_SECONDS, TERMINAL_FALLBACK_SECONDS
    from avid.services.runs import RunRegistry
    from avid.web import sse

    assert svc.STREAM_HEARTBEAT_SECONDS is STREAM_HEARTBEAT_SECONDS
    assert svc.TERMINAL_FALLBACK_SECONDS is TERMINAL_FALLBACK_SECONDS
    assert sse.HEARTBEAT_SECONDS is STREAM_HEARTBEAT_SECONDS
    default = inspect.signature(RunRegistry.subscribe).parameters["heartbeat"].default
    assert default is STREAM_HEARTBEAT_SECONDS
