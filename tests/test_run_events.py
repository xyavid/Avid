"""B1 / B2 / B3：事件序列与重放（无 UI 也能验收）。

F0 的价值不依赖前端：它把「工具是否开始过」「压缩是否发生过」「审批被谁拒绝」
变成可断言的事实。这里跑的是真的 ``svc`` 注册表 + 真的会话落盘，只把模型换成
脚本、把工具换成记录器。
"""

from __future__ import annotations

import threading

from avid.runtime import events
from avid.svc import Services
from support import (
    RecordingTools,
    ScriptedChat,
    collect,
    make_turn,
    new_session,
    tool_call,
    wait_terminal,
)


def build(root, chat, tools=None, **kwargs) -> Services:
    return Services(
        root=root / ".avid" / "sessions", chat=chat, tool_registry=tools, **kwargs
    )


def run_to_end(services: Services, prompt: str = "跑一下"):
    session_id = new_session(services)
    record = services.runs.start(session_id, prompt)
    assert wait_terminal(record), f"运行没结束：{record.status}"
    return record


def many_rounds(count: int = 4) -> ScriptedChat:
    turns = [
        make_turn("", [tool_call("read_file", '{"path": "f.txt"}', f"call_{i}")])
        for i in range(1, count + 1)
    ]
    return ScriptedChat(*turns, make_turn("完成"))


# ---------------- B1 ----------------


def test_event_sequence_is_ordered_and_durable_seq_is_monotonic(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    record = run_to_end(services)

    got = collect(services, record.run_id)
    types = [event.type for event in got]

    assert types[0] == events.RUN_STARTED
    assert types[-1] == events.RUN_FINISHED
    assert events.USER_MESSAGE in types
    assert events.APPROVAL_REQUESTED not in types  # 只读工具不需要审批
    assert types.count(events.TOOL_CALL_STARTED) == 2
    assert types.count(events.TOOL_CALL_FINISHED) == 2
    assert types.count(events.TOOL_RESULT_MESSAGE) == 2
    assert types.count(events.ASSISTANT_MESSAGE) == 3

    # durable 的 seq 严格递增、不重复、从 1 开始
    seqs = [event.seq for event in got if event.seq is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    assert record.next_seq == seqs[-1] + 1

    # transient 不带 seq
    transient = [event for event in got if event.type == events.RUN_STATUS]
    assert transient
    assert all(event.seq is None for event in transient)

    # 同一 run 的事件都带同一个 run_id；消息事件都带 entry_id
    assert {event.run_id for event in got} == {record.run_id}
    for event in got:
        if event.type in (
            events.USER_MESSAGE,
            events.ASSISTANT_MESSAGE,
            events.TOOL_RESULT_MESSAGE,
        ):
            assert event.data["entry_id"]
            assert event.data["message"]["role"] in ("user", "assistant", "tool")

    # run_finished 是提示，权威事实是注册表状态 + 已提交条目
    assert record.status == "finished"
    assert record.text == "完成"
    assert services.sessions.get(record.session_id)["message_count"] == len(
        [e for e in got if e.type in (events.USER_MESSAGE, events.ASSISTANT_MESSAGE, events.TOOL_RESULT_MESSAGE)]
    )


def test_tool_events_carry_tool_call_id_and_no_full_content_on_the_wire(sandbox):
    tools = RecordingTools({"read_file": "内容" * 10}).registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")), tools)
    record = run_to_end(services)

    finished = [e for e in collect(services, record.run_id) if e.type == events.TOOL_CALL_FINISHED]
    assert len(finished) == 1
    assert finished[0].data["tool_call_id"] == "call_1"
    assert finished[0].data["truncated"] is False
    assert finished[0].data["duration_ms"] >= 0


# ---------------- B2 ----------------


def test_cursor_replay_is_exact(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools)
    record = run_to_end(services)

    everything = collect(services, record.run_id)
    total = [event.seq for event in everything if event.seq is not None]
    assert len(total) >= 12, f"样本太小，补齐测不出东西：{total}"

    for after in (0, 5, 11):
        replayed = collect(services, record.run_id, after=after)
        seqs = [event.seq for event in replayed if event.seq is not None]
        assert seqs == [seq for seq in total if seq > after]
        assert len(seqs) == len(set(seqs))


# ---------------- B3 ----------------


def test_buffer_eviction_is_explicit_resync(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools, buffer_size=2)
    record = run_to_end(services)

    got = collect(services, record.run_id, after=0)
    assert got, "订阅应当至少收到一条 resync"
    assert got[0].type == events.RESYNC
    assert got[0].data["reason"] == "buffer_evicted"
    assert got[0].seq is not None  # resync 自己是 durable 的


def test_fresh_cursor_within_buffer_does_not_resync(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("直接回答")), tools)
    record = run_to_end(services)

    got = collect(services, record.run_id, after=0)
    assert events.RESYNC not in [event.type for event in got]


# ---------------- B1（F3）：delta 通道 ----------------


def streaming_reply(pieces: tuple[str, ...], reply: str):
    """假的流式调用：按分片回调，最后返回一条与非流式同形的 Turn。"""

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        for piece in pieces:
            if on_delta is not None:
                on_delta(piece)
        return make_turn(reply)

    return fake_stream


def test_deltas_are_opt_in_and_carry_no_seq(sandbox, monkeypatch):
    """C13 的内核侧：同一批 delta，订阅了才投递；不参与游标补齐，也不改最终状态（I5）。

    delta 不重放（I15），所以只能在**流式过程中**观察它。用一道闸门把假模型卡在模型
    调用里，等两个订阅者（一个订阅、一个不订阅）都进入实时跟随后再放行。
    """
    entered = threading.Event()
    release = threading.Event()

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        entered.set()
        release.wait(timeout=5)
        on_delta("你")
        on_delta("好")
        return make_turn("你好")

    monkeypatch.setattr("avid.svc.runs.stream_completion", fake_stream)
    services = Services(root=sandbox / ".avid" / "sessions")
    session_id = new_session(services)
    record = services.runs.start(session_id, "打个招呼")
    assert entered.wait(timeout=5), "运行没走到模型调用"

    def consume(*, deltas: bool):
        got: list = []
        attached = threading.Event()

        def pump():
            for event in services.runs.subscribe(record.run_id, deltas=deltas):
                if event is None:
                    continue
                got.append(event)
                attached.set()

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        assert attached.wait(timeout=5), "订阅没拿到重放的事件"
        return got, thread

    with_deltas, watcher = consume(deltas=True)
    without_deltas, silent = consume(deltas=False)
    release.set()
    watcher.join(timeout=5)
    silent.join(timeout=5)

    deltas = [event for event in with_deltas if event.type == events.ASSISTANT_DELTA]
    assert [event.data["text"] for event in deltas] == ["你", "好"]
    assert all(event.seq is None for event in deltas), "delta 不参与游标补齐（I4）"
    assert events.ASSISTANT_DELTA not in [event.type for event in without_deltas]

    # durable 消息仍带**完整**内容：delta 全丢也不影响正确性（I5）。
    finals = [event for event in with_deltas if event.type == events.ASSISTANT_MESSAGE]
    assert finals[-1].data["message"]["content"] == "你好"

    # delta 不重放：跑完之后从 0 补齐也拿不到它（I15）。
    assert wait_terminal(record)
    replayed = collect(services, record.run_id, after=0, deltas=True)
    assert events.ASSISTANT_DELTA not in [event.type for event in replayed]


def test_many_deltas_do_not_evict_durable_events(sandbox, monkeypatch):
    """重放预算按 durable 计数：一次长回复的 delta 不该把 durable 挤出缓冲。

    否则 delta 的**多少**会左右 I5 的语义——流得久一点，重连的客户端就平白收到
    resync。这里 50 条 delta + 4 条 durable，缓冲上限压到 10：按总条数淘汰会丢掉
    durable，按 durable 计数则一条都不丢。
    """
    monkeypatch.setattr(
        "avid.svc.runs.stream_completion",
        streaming_reply(tuple(f"片{index}" for index in range(50)), "".join(f"片{index}" for index in range(50))),
    )
    services = Services(root=sandbox / ".avid" / "sessions", buffer_size=10)
    session_id = new_session(services)
    record = services.runs.start(session_id, "长回复")
    assert wait_terminal(record)

    assert record.evicted_upto == 0, "delta 把 durable 挤出了重放缓冲"
    got = collect(services, record.run_id, after=0)
    assert events.RESYNC not in [event.type for event in got]


# ---------------- 会话句柄的并发（阶段 18 修掉的竞态） ----------------


def test_reading_while_a_run_starts_never_double_opens_the_session(sandbox):
    """读路径与运行路径会同时想开会话，而会话层只允许一个句柄。

    修之前：``start`` 先登记 ``_active`` 再在运行线程里 ``open``，窗口期内读取
    看到"有活动 run 但拿不到句柄"，就自己开——同一会话被开两次，运行刚起就
    failed（``会话已经打开``），或读取 500（``会话已关闭``）。修法是把
    「开句柄」放进每会话一把的句柄锁，并让 ``_active`` 与句柄同时可见。
    """
    services = build(sandbox, ScriptedChat(make_turn("答")))
    session_id = new_session(services)
    failures: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                services.sessions.get(session_id)
                services.sessions.list_sessions()
            except Exception as exc:  # 读路径任何异常都算失败
                failures.append(f"{type(exc).__name__}: {exc}")
                return

    readers = [threading.Thread(target=reader) for _ in range(3)]
    for thread in readers:
        thread.start()
    try:
        record = services.runs.start(session_id, "跑一下")
        assert wait_terminal(record), record.status
    finally:
        stop.set()
        for thread in readers:
            thread.join(timeout=5)

    assert failures == []
    assert record.status == "finished", record.status
    assert services.sessions.get(session_id)["message_count"] >= 2
