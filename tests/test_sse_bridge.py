"""``subscribe_async``: event subscription that does not hold a threadpool thread.

Replay / gap / resync semantics match the sync ``subscribe`` (one source in ``svc/runs.py``); the
cases below pin the same sequence for both, live wakeups through the bridge, and watcher cleanup.
"""

from __future__ import annotations

import asyncio
import threading
import time

from support import ScriptedChat, make_turn, new_session, wait_terminal

from avid.agent.events import RUN_FINISHED, RUN_STARTED, TERMINAL_EVENT_TYPES
from avid.services import MAX_CONCURRENT_STREAMS, Services


def build(root, chat, **kwargs) -> Services:
    return Services(root=root / ".avid" / "sessions", chat=chat, **kwargs)


def drain_async(services: Services, run_id: str, *, after: int = 0, timeout: float = 5.0):
    """Drain one async subscription in a fresh event loop; returns the events."""

    async def one():
        got = []
        agen = services.runs.subscribe_async(run_id, after=after)
        try:
            async for event in agen:
                got.append(event)
                if event.type in TERMINAL_EVENT_TYPES:
                    break
        finally:
            await agen.aclose()
        return got

    return asyncio.run(asyncio.wait_for(one(), timeout))


def test_async_subscription_replays_the_same_sequence_as_sync(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("完成")))
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert wait_terminal(record), f"运行没结束：{record.status}"

    async_events = drain_async(services, record.run_id)
    sync_events = list(services.runs.subscribe(record.run_id))

    assert [e.type for e in async_events] == [e.type for e in sync_events]
    assert [e.seq for e in async_events] == [e.seq for e in sync_events]
    assert [e.type for e in async_events][0] == RUN_STARTED
    assert [e.type for e in async_events][-1] == RUN_FINISHED


def test_async_subscription_follows_live_events_via_the_bridge(sandbox):
    """Subscription first, events later: the bridge must wake the loop on the run thread's emit."""
    gate = threading.Event()

    def chat(config, messages, **kwargs):
        assert gate.wait(3.0), "测试没放行"
        return make_turn("完成")

    services = build(sandbox, chat)
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")

    got: list = []
    done = threading.Event()

    def drain():
        async def one():
            async for event in services.runs.subscribe_async(record.run_id):
                got.append(event)
                if event.type in TERMINAL_EVENT_TYPES:
                    return

        asyncio.run(one())
        done.set()

    threading.Thread(target=drain, daemon=True).start()

    for _ in range(200):
        if record.watchers:
            break
        time.sleep(0.01)
    assert record.watchers, "订阅者没有注册到桥上"

    gate.set()
    assert done.wait(5.0), "订阅者没被唤醒到终态"

    types = [event.type for event in got]
    assert types[0] == RUN_STARTED
    assert types[-1] == RUN_FINISHED


def test_watcher_is_removed_when_the_generator_closes(sandbox):
    services = build(sandbox, ScriptedChat(make_turn("完成")))
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert wait_terminal(record)

    async def one():
        agen = services.runs.subscribe_async(record.run_id)
        async for _ in agen:
            break  # take one event, then close
        await agen.aclose()

    asyncio.run(asyncio.wait_for(one(), 5.0))

    assert record.watchers == []


def test_many_concurrent_async_subscribers_all_get_full_replay(sandbox):
    """40 concurrent subscribers: the async path holds no threads, so replays never interfere."""
    services = build(sandbox, ScriptedChat(make_turn("完成")))
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert wait_terminal(record)

    async def one():
        got = []
        agen = services.runs.subscribe_async(record.run_id)
        try:
            async for event in agen:
                got.append(event.type)
                if event.type in TERMINAL_EVENT_TYPES:
                    break
        finally:
            await agen.aclose()
        return got

    async def all():
        return await asyncio.gather(*(one() for _ in range(40)))

    results = asyncio.run(asyncio.wait_for(all(), 10.0))

    assert len(results) == 40
    for types in results:
        assert types[0] == RUN_STARTED
        assert types[-1] == RUN_FINISHED


def test_stream_cap_is_now_a_guardrail_not_a_threadpool_shadow():
    """The cap is 256: it guards against runaway clients, not a threadpool starving REST."""
    assert MAX_CONCURRENT_STREAMS >= 256
