"""``subscribe_async``：不占线程池线程的事件订阅（阶段 30d）。

语义与同步 ``subscribe`` 同源（重放/缺口/resync 都在 ``svc/runs.py`` 一处），
这里的用例钉三件事：**同形**（同一份运行，两个订阅者拿到同一条事件序列）、
**活订阅**（桥线程真能唤醒事件循环）、**清理**（生成器关闭后 watcher 摘除）。
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
    """在独立事件循环里跑完一个异步订阅，返回事件列表。"""

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
    """订阅先挂上、事件后发生：桥线程必须把运行线程的 emit 唤醒到事件循环。"""
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
            break  # 只拿一条就关
        await agen.aclose()

    asyncio.run(asyncio.wait_for(one(), 5.0))

    assert record.watchers == []


def test_many_concurrent_async_subscribers_all_get_full_replay(sandbox):
    """40 条并发订阅（高于旧上限 24）：异步路径不占线程，全量重放互不干扰。"""
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
    """上限放开到 256：它防的是失控客户端，不再是 REST 被线程饿死的那道墙。"""
    assert MAX_CONCURRENT_STREAMS >= 256
