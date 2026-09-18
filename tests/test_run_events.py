"""B1 / B2 / B3：事件序列与重放（无 UI 也能验收）。

F0 的价值不依赖前端：它把「工具是否开始过」「压缩是否发生过」「审批被谁拒绝」
变成可断言的事实。这里跑的是真的 ``svc`` 注册表 + 真的会话落盘，只把模型换成
脚本、把工具换成记录器。
"""

from __future__ import annotations

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
