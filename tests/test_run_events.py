"""B1 / B2 / B3: event ordering and replay, verifiable without a UI.

Runs the real services registry and real session writes; only the model and the tools are
swapped for scripts and recorders.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest
from support import (
    RecordingTools,
    ScriptedChat,
    collect,
    make_turn,
    new_session,
    tool_call,
    wait_for,
    wait_handle_released,
    wait_terminal,
)

from avid.agent.events import (
    APPROVAL_REQUESTED,
    ASSISTANT_DELTA,
    ASSISTANT_MESSAGE,
    DELTA_EVENT_TYPES,
    REASONING_DELTA,
    RESYNC,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    RUN_STATUS,
    TODO_REMINDER,
    TOOL_CALL_FINISHED,
    TOOL_CALL_STARTED,
    TOOL_RESULT_MESSAGE,
    USER_MESSAGE,
)
from avid.providers.client import LLMError
from avid.services import Services, runs
from avid.services.errors import RunNotFound
from avid.session import (
    BranchScan,
    EntryQuery,
    SessionRecorder,
    SessionStorageError,
    branch_compaction,
    messages_for_branch,
)
from avid.session.types import NOTICE_ENTRY


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

    assert types[0] == RUN_STARTED
    assert types[-1] == RUN_FINISHED
    assert USER_MESSAGE in types
    assert APPROVAL_REQUESTED not in types  # read-only tools need no approval
    assert types.count(TOOL_CALL_STARTED) == 2
    assert types.count(TOOL_CALL_FINISHED) == 2
    assert types.count(TOOL_RESULT_MESSAGE) == 2
    assert types.count(ASSISTANT_MESSAGE) == 3

    # durable seq is strictly increasing, unique and starts at 1
    seqs = [event.seq for event in got if event.seq is not None]
    assert seqs == list(range(1, len(seqs) + 1))
    assert record.next_seq == seqs[-1] + 1

    # transient events carry no seq
    transient = [event for event in got if event.type == RUN_STATUS]
    assert transient
    assert all(event.seq is None for event in transient)

    # every event carries the run_id; message events also carry entry_id
    assert {event.run_id for event in got} == {record.run_id}
    for event in got:
        if event.type in (
            USER_MESSAGE,
            ASSISTANT_MESSAGE,
            TOOL_RESULT_MESSAGE,
        ):
            assert event.data["entry_id"]
            assert event.data["message"]["role"] in ("user", "assistant", "tool")

    # run_finished is a hint; the authority is the registry status plus committed entries
    assert record.status == "finished"
    assert record.text == "完成"
    assert services.sessions.get(record.session_id)["message_count"] == len(
        [e for e in got if e.type in (USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT_MESSAGE)]
    )


def test_tool_events_carry_tool_call_id_and_no_full_content_on_the_wire(sandbox):
    tools = RecordingTools({"read_file": "内容" * 10}).registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")), tools)
    record = run_to_end(services)

    finished = [e for e in collect(services, record.run_id) if e.type == TOOL_CALL_FINISHED]
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
    assert got[0].type == RESYNC
    assert got[0].data["reason"] == "buffer_evicted"
    assert got[0].seq is not None  # resync is durable itself


def test_fresh_cursor_within_buffer_does_not_resync(sandbox):
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("直接回答")), tools)
    record = run_to_end(services)

    got = collect(services, record.run_id, after=0)
    assert RESYNC not in [event.type for event in got]


# ---------------- B1: the delta channel ----------------


def streaming_reply(pieces: tuple[str, ...], reply: str):
    """Fake streaming call: emits pieces through on_delta, then returns a Turn shaped as usual."""

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        for piece in pieces:
            if on_delta is not None:
                on_delta(piece)
        return make_turn(reply)

    return fake_stream


def test_deltas_are_opt_in_and_carry_no_seq(sandbox, monkeypatch):
    """Deltas are opt-in, carry no seq, never fill a replay cursor and never change the final
    state (I5); they are not replayed (I15), so only a live subscriber sees them.
    """
    entered = threading.Event()
    release = threading.Event()

    def fake_stream(config, messages, *, on_delta=None, **kwargs):
        entered.set()
        release.wait(timeout=5)
        on_delta("你")
        on_delta("好")
        return make_turn("你好")

    monkeypatch.setattr("avid.services.runs.stream_completion", fake_stream)
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

    deltas = [event for event in with_deltas if event.type == ASSISTANT_DELTA]
    assert [event.data["text"] for event in deltas] == ["你", "好"]
    assert all(event.seq is None for event in deltas), "delta 不参与游标补齐（I4）"
    assert ASSISTANT_DELTA not in [event.type for event in without_deltas]

    # durable messages still carry full content: losing every delta does not affect correctness
    finals = [event for event in with_deltas if event.type == ASSISTANT_MESSAGE]
    assert finals[-1].data["message"]["content"] == "你好"

    # deltas are not replayed: replaying from 0 after the run yields none either
    assert wait_terminal(record)
    replayed = collect(services, record.run_id, after=0, deltas=True)
    assert ASSISTANT_DELTA not in [event.type for event in replayed]


def test_many_deltas_do_not_evict_durable_events(sandbox, monkeypatch):
    """The replay budget counts durable events: 50 deltas plus 4 durable ones under a cap of 10
    must evict nothing, or a long stream alone could push a reconnecting client into resync.
    """
    monkeypatch.setattr(
        "avid.services.runs.stream_completion",
        streaming_reply(tuple(f"片{index}" for index in range(50)), "".join(f"片{index}" for index in range(50))),
    )
    services = Services(root=sandbox / ".avid" / "sessions", buffer_size=10)
    session_id = new_session(services)
    record = services.runs.start(session_id, "长回复")
    assert wait_terminal(record)

    assert record.evicted_upto == 0, "delta 把 durable 挤出了重放缓冲"
    got = collect(services, record.run_id, after=0)
    assert RESYNC not in [event.type for event in got]


# ---------------- session handle concurrency ----------------


def test_reading_while_a_run_starts_never_double_opens_the_session(sandbox):
    """Read and run paths want the session handle at the same time while the session layer allows
    one handle, so opening goes under the per-session handle lock together with publishing
    ``_active``.
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
            except Exception as exc:  # any read-path exception counts as a failure
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


# ---------------- run record cancellation and stats ----------------


def test_reasoning_delta_is_a_delta_tier_event(sandbox):
    """Reasoning deltas are delta-tier: not delivered by default, not persisted, not budgeted."""
    services = build(sandbox, ScriptedChat(make_turn("答")), buffer_size=8)
    record = services.runs.get(services.runs.start(new_session(services), "跑").run_id)

    services.runs.emit_delta(
        record, services.runs, "它在想", event_type=REASONING_DELTA
    )

    deltas = [event for event in record.events if event.seq is None]
    assert [event.type for event in deltas] == [REASONING_DELTA]
    assert deltas[0].data == {"text": "它在想"}
    assert REASONING_DELTA in DELTA_EVENT_TYPES


def test_delta_bookkeeping_keeps_the_buffer_consistent(sandbox):
    """Incremental delta bookkeeping must equal a full recompute: the durable count stays within
    the cap, absolute indices stay self-consistent, and evicted_upto is the highest dropped seq.
    """
    services = build(sandbox, ScriptedChat(make_turn("答")), buffer_size=8)
    record = services.runs.get(services.runs.start(new_session(services), "跑").run_id)

    for index in range(50):
        services.runs.emit(record, RUN_STATUS, round=index)  # transient
        services.runs.emit_delta(record, services.runs, f"t{index}")
        if index % 7 == 0:
            services.runs.emit(record, TOOL_RESULT_MESSAGE, entry_id=str(index))

    durable = [event for event in record.events if event.seq is not None]
    assert len(durable) <= 8
    assert record.durable_index == [
        record.dropped + position
        for position, event in enumerate(record.events)
        if event.seq is not None
    ]
    assert record.absolute_index() == record.dropped + len(record.events)
    if record.evicted_upto:
        assert all(event.seq is None or event.seq > record.evicted_upto for event in record.events)


def test_finished_runs_and_session_locks_are_reclaimed(sandbox):
    """Terminal records and session locks are both reclaimable, while a just-finished record stays
    inside its retention window.
    """
    services = build(sandbox, ScriptedChat(make_turn("答")), max_runs=1)
    record = run_to_end(services)

    # Just finished and still inside the retention window: the count fallback must not touch it.
    assert services.runs.get(record.run_id) is record
    services.runs._sweep()
    assert services.runs.get(record.run_id) is record, "刚结束的记录不该被兜底淘汰"
    assert services.runs._session_locks == {}, "运行结束后句柄锁应当已被摘掉"

    # Past the window the record and its buffer are dropped, so a lookup becomes 404.
    services.runs._retention_ms = 0
    record.finished_at = 0
    services.runs._sweep()
    with pytest.raises(RunNotFound):
        services.runs.get(record.run_id)
    assert record.events == [], "缓冲要一起释放"
    assert services.runs._session_locks == {}


# ---------------- failure paths: every code becomes an observable terminal state ----------------


def _raising_chat(exc: Exception):
    def chat(config, messages, **kwargs):
        raise exc

    return chat


@pytest.mark.parametrize(
    "exc,code",
    [
        (LLMError("模型挂了"), "llm_error"),
        (RuntimeError("程序错误"), "internal"),
    ],
    ids=["llm_error", "internal"],
)
def test_chat_failures_become_a_terminal_event_with_the_right_code(sandbox, exc, code):
    """A failure is an observable terminal state: the record carries a code and the stream carries
    run_failed, never run_finished.
    """
    services = build(sandbox, _raising_chat(exc))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "failed"
    assert record.error is not None
    assert record.error["code"] == code
    assert str(exc) in record.error["message"]

    frames = collect(services, record.run_id)
    failed = [event for event in frames if event.type == RUN_FAILED]
    assert failed, [event.type for event in frames]
    assert failed[-1].data["code"] == code
    assert RUN_FINISHED not in [event.type for event in frames]


def test_a_run_longer_than_the_old_cap_still_finishes(sandbox):
    """No round cap exists in the kernel: 8 tool rounds still run to the model's own finish
    (regression against the old hard limit of 8, which failed as run_failed{round_limit}).
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(8), tools)
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "finished"
    assert record.text == "完成"


def test_a_run_of_forty_rounds_still_finishes(sandbox):
    """No higher cutoff either: 40 tool rounds still end as finished, not budget-exhausted."""
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(40), tools)
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.status == "finished"
    assert record.error is None


def test_config_error_failure_is_mapped(sandbox, monkeypatch):
    """A missing model config surfaces inside the run thread as config_error, not a dead thread."""
    # BYOK config points at a file that does not exist, so resolve_chat has no model
    monkeypatch.setenv("AVID_BYOK_CONFIG", str(sandbox / "none" / "models.json"))
    services = build(sandbox, ScriptedChat(make_turn("答")))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.error["code"] == "config_error"


def test_session_error_failure_is_mapped(sandbox, monkeypatch):
    """A session-layer failure (corrupt file while reading history) maps to session_error."""

    def boom(session, branch):
        raise SessionStorageError("会话文件坏了")

    monkeypatch.setattr(runs, "messages_for_branch", boom)
    services = build(sandbox, ScriptedChat(make_turn("答")))
    record = services.runs.start(new_session(services), "跑")

    assert wait_terminal(record), record.status
    assert record.error["code"] == "session_error"


# ---------------- buffer bounds: live-follow gaps and the total event cap ----------------


class BlockingChat:
    """Blocks the first model call so the test can touch the buffer while the run is live."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, config, messages, **kwargs):
        self.entered.set()
        assert self.release.wait(5), "测试自己没放行"
        return make_turn("答")


def test_live_follower_is_told_when_its_cursor_is_evicted(sandbox):
    """A resync check at subscribe time is not enough: when the buffer turns over mid-follow the
    live subscriber must be told explicitly, never quietly served from the new head (I5).
    """
    chat = BlockingChat()
    services = build(sandbox, chat, buffer_size=2)
    record = services.runs.start(new_session(services), "跑")
    assert chat.entered.wait(5)

    got: list[str] = []
    consumed = threading.Event()

    def consume() -> None:
        # slow consumer: pause between next() calls to simulate a client that cannot keep up
        for event in services.runs.subscribe(record.run_id, after=0):
            if event is not None:
                got.append(event.type)
                consumed.set()
            time.sleep(0.05)

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    # first let the subscriber drain existing events and start following (nothing evicted yet)
    assert consumed.wait(5), "订阅者没开始消费"
    assert record.evicted_upto == 0, "这一步不该有缺口，否则测不到跟随期路径"

    # while the subscriber sleeps, emit far more durable events than buffer_size: it gets evicted
    for index in range(6):
        services.runs.emit(
            record,
            TOOL_CALL_STARTED,
            tool="bash",
            arguments={},
            round=1,
            tool_call_id=f"c{index}",
        )

    assert wait_for(lambda: RESYNC in got, 5), got
    chat.release.set()
    assert wait_terminal(record)
    thread.join(timeout=5)


def test_delta_flood_cannot_grow_the_buffer_without_bound(sandbox):
    """Every delta is a RunEvent, so deltas are capped too: the cap evicts the oldest prefix, and
    any durable event dropped with it must be recorded in ``evicted_upto``.
    """
    chat = BlockingChat()
    services = build(sandbox, chat, buffer_size=16, max_events=64)
    record = services.runs.start(new_session(services), "跑")
    assert chat.entered.wait(5)

    for index in range(400):
        services.runs.emit(
            record,
            TOOL_CALL_STARTED,
            tool="bash",
            arguments={},
            round=1,
            tool_call_id=f"c{index}",
        )
        services.runs.emit_delta(record, services.runs, "x")

    assert len(record.events) <= 64, len(record.events)
    assert record.evicted_upto > 0, "被丢掉的 durable 必须记账，否则是静默缺口"
    assert record.durable_index == [
        record.dropped + position
        for position, event in enumerate(record.events)
        if event.seq is not None
    ]
    assert record.absolute_index() == record.dropped + len(record.events)

    chat.release.set()
    assert wait_terminal(record)


def test_cancel_arriving_before_the_state_exists_is_not_lost(sandbox, monkeypatch):
    """A cancel arriving before ``record.state`` exists must be replayed once: ``cancel()`` only
    reaches ``state.cancel()`` when the state is ready, and the loop watches ``state.cancelled``.
    """
    entered = threading.Event()
    release = threading.Event()
    real = runs.messages_for_branch

    def slow(session, branch):
        entered.set()
        assert release.wait(5), "测试自己没放行"
        return real(session, branch)

    monkeypatch.setattr(runs, "messages_for_branch", slow)

    services = build(sandbox, ScriptedChat(make_turn("答")))
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert entered.wait(5), "运行线程没走到读历史那一步"

    services.runs.cancel(record.run_id)  # record.state is still None here
    release.set()

    assert wait_terminal(record), record.status
    assert record.status == "cancelled", record.status
    assert record.cancel_reason == "user"


def test_run_record_reports_the_real_round_and_tokens(sandbox):
    """``GET /api/runs/{id}`` reports round/tokens from the state, not a stale 0: many_rounds(2)
    is two tool rounds plus a final round, so round reaches 3 and tokens accumulate per round.
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    session_id = new_session(services)
    record = services.runs.start(session_id, "跑一下")
    assert wait_terminal(record), record.status

    payload = services.runs.get(record.run_id).to_dict()
    assert payload["round"] >= 3, payload
    assert payload["tokens"] > 0, payload


# ---------------- entry type of injected reminders ----------------


def test_plan_travels_in_the_tail_not_in_the_history(sandbox):
    """The plan rides the per-round tail block: not persisted, no event, never a user role."""

    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(4), tools)
    record = run_to_end(services)

    entries = services.sessions.entries(record.session_id, order="asc")["entries"]
    assert [entry for entry in entries if entry["type"] == NOTICE_ENTRY] == []

    kinds = [event.type for event in collect(services, record.run_id)]
    assert kinds.count(TODO_REMINDER) == 0
    assert kinds.count(USER_MESSAGE) == 1

    # Projection invariant: the stored history holds no kernel-written reminder text.
    # Direct file reads wait for the run thread to hand the handle back, then take the handle
    # lock: record.terminal only reports the registry state (see support.wait_handle_released).
    wait_handle_released(services, record.session_id)
    with services.runs.session_lock(record.session_id):
        session = services.repo.open(services.runs.find_metadata(record.session_id))
        try:
            history = [
                str(message.get("content") or "")
                for message in messages_for_branch(session)
            ]
        finally:
            session.close()
    assert not any("[提醒]" in text for text in history)


# ---------------- usage snapshots ----------------

#: Scripted model usage (``support.make_turn``): prompt=1 / completion=2 / total=3.
SCRIPT_USAGE = {"context": {"tokens": 1, "window": None, "utilization": None}}


def test_run_status_carries_the_usage_snapshot_every_round(sandbox):
    """Every model call leaves a snapshot in one schema; the UI reads occupancy and cache hits."""
    tools = RecordingTools().registry("read_file")
    services = build(
        sandbox,
        ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")),
        tools,
    )
    record = run_to_end(services)

    snapshots = [
        event.data["usage"]
        for event in collect(services, record.run_id)
        if event.type == RUN_STATUS and "usage" in event.data
    ]
    assert len(snapshots) == 2  # two rounds, two snapshots
    context = snapshots[-1]["context"]
    assert context["tokens"] == SCRIPT_USAGE["context"]["tokens"]
    assert context["window"] is None
    # parts: the three blocks sum to the real total (system, tools, messages as the remainder)
    parts = context["parts"]
    assert parts is not None
    assert sum(parts.values()) == context["tokens"]
    # test-model is not in the built-in window table: no denominator, no guessed utilization
    assert snapshots[-1]["context"]["window"] is None
    # the scripted model reports no cache counters: None (shown as "-"), not 0
    assert snapshots[-1]["cache"] == {
        "read_tokens": None,
        "write_tokens": None,
        "hit_ratio": None,
    }
    assert snapshots[-1]["compaction"]["count"] == 0


def test_terminal_event_and_rest_view_share_the_final_snapshot(sandbox):
    """run_finished carries the final snapshot (durable, delivered in-stream) and REST agrees."""
    tools = RecordingTools().registry("read_file")
    services = build(
        sandbox,
        ScriptedChat(make_turn("", [tool_call("read_file")]), make_turn("好")),
        tools,
    )
    record = run_to_end(services)
    events_list = collect(services, record.run_id)

    finished = next(e for e in events_list if e.type == RUN_FINISHED)
    assert finished.data["usage"] == record.usage
    # exit reason: every exit is named (StopReason)
    assert finished.data["reason"] == "final_text"
    assert record.usage["context"]["tokens"] == 1
    # accumulated tokens are the run bill (two rounds x total 3), not occupancy
    assert record.tokens == 6
    assert services.runs.get(record.run_id).to_dict()["usage"] == record.usage


def test_usage_is_persisted_into_the_session_for_the_branch(sandbox):
    """Persisted: the branch list carries that branch's latest run snapshot across restarts."""
    from avid.session import USAGE_NS, branch_usage

    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(make_turn("答")), tools)
    record = run_to_end(services)

    listed = services.sessions.list_branches(record.session_id)["branches"]
    main = next(item for item in listed if item["name"] == "main")
    assert main["usage"]["context"]["tokens"] == 1

    # direct file read: wait for the handle release, then open under the handle lock
    wait_handle_released(services, record.session_id)
    with services.runs.session_lock(record.session_id):
        session = services.repo.open(services.runs.find_metadata(record.session_id))
        try:
            stored = session.scan_values(USAGE_NS)
        finally:
            session.close()
    assert [item.key for item in stored] == ["main"]
    assert stored[0].value == main["usage"]
    # the address builder matches the write side (same namespace and key)
    assert branch_usage("main").key == "main"


def test_terminal_flag_and_terminal_event_land_together(sandbox):
    """``record.terminal`` true implies the terminal event is already in the buffer: subscribers
    decide from it whether more events can come, so the flag and the event must land together.
    """
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, many_rounds(2), tools)
    record = run_to_end(services)
    assert record.terminal

    got = collect(services, record.run_id)
    assert got, "订阅一条事件都没收到"
    assert got[-1].type == RUN_FINISHED


def test_loop_records_the_three_prompt_parts(sandbox):
    """The parts are really recorded: a large usage (1000 prompt tokens) is injected because the
    scripted usage of 1 token would leave only one block with a nonzero share.
    """
    from avid.providers.client import Turn, Usage

    turn = Turn(
        message={"role": "assistant", "content": "好"},
        text="好",
        tool_calls=[],
        usage=Usage(prompt_tokens=1_000, completion_tokens=5, total_tokens=1_005),
        model="m",
        finish_reason="stop",
    )
    tools = RecordingTools().registry("read_file")
    services = build(sandbox, ScriptedChat(turn), tools)
    record = run_to_end(services)

    snapshots = [
        event.data["usage"]["context"]
        for event in collect(services, record.run_id)
        if event.type == RUN_STATUS and "usage" in event.data
    ]
    parts = snapshots[-1]["parts"]
    assert parts is not None
    assert sum(parts.values()) == 1_000
    assert parts["system"] > 0, parts   # system prompt
    assert parts["tools"] > 0, parts    # tool definitions (every TOOLS entry as JSON)
    assert parts["messages"] > 0, parts  # conversation messages (the remainder)


# ---------------- in-session commands ----------------


def test_compact_command_finishes_without_calling_the_model(sandbox):
    """'/compact' takes the command branch: no model call, run_finished carries reason command."""
    # empty ScriptedChat: being called at all would trip its assertion
    services = build(sandbox, ScriptedChat())
    record = run_to_end(services, prompt="/compact")

    assert record.status == "finished"
    assert "没有可压缩的更早历史" in record.text
    finished = next(
        e for e in collect(services, record.run_id) if e.type == RUN_FINISHED
    )
    assert finished.data["reason"] == "command"


def test_unknown_command_answers_with_help_text(sandbox):
    services = build(sandbox, ScriptedChat())
    record = run_to_end(services, prompt="/nope")

    assert record.status == "finished"
    assert "/compact" in record.text


def test_skill_command_feeds_the_skill_body_as_the_user_message(sandbox):
    """'/demo' feeds the skill body as the user message: the model receives exactly that text."""
    (sandbox / "skills" / "demo").mkdir(parents=True)
    (sandbox / "skills" / "demo" / "SKILL.md").write_text(
        "---\ndescription: 演示技能\n---\n这是演示技能的正文", encoding="utf-8"
    )
    chat = ScriptedChat(make_turn("照做"))
    services = build(sandbox, chat)
    record = run_to_end(services, prompt="/demo")

    assert record.status == "finished"
    assert record.text == "照做"
    first_request = chat.requests[0]["messages"]
    assert any("这是演示技能的正文" in str(m.get("content")) for m in first_request)


@contextmanager
def open_session(services, session_id: str):
    """Read the session file directly: wait for the handle release, then take the handle lock
    before open (the run thread releases under the same lock, so no SessionAlreadyOpenError).
    """
    wait_handle_released(services, session_id)
    with services.runs.session_lock(session_id):
        session = services.repo.open(services.runs.find_metadata(session_id))
        try:
            yield session
        finally:
            session.close()


def test_rewind_command_rolls_back_the_last_user_turn(sandbox):
    """'/rewind' moves the tip back, clears the compaction cursor and restores files to before that
    input, ending with reason command (two real write_file rounds without a registry override).
    """
    chat = ScriptedChat(
        make_turn("", [tool_call("write_file", '{"path": "notes.txt", "content": "第一版"}')]),
        make_turn("第一答"),
        make_turn(
            "",
            [
                tool_call("write_file", '{"path": "notes.txt", "content": "第二版"}', "call_2"),
                tool_call("write_file", '{"path": "created.txt", "content": "第二轮新建"}', "call_3"),
            ],
        ),
        make_turn("第二答"),
    )
    services = build(sandbox, chat)
    session_id = new_session(services)

    first = services.runs.start(session_id, "第一问", auto_approve=True)
    assert wait_terminal(first)
    second = services.runs.start(session_id, "第二问", auto_approve=True)
    assert wait_terminal(second)
    assert (sandbox / "notes.txt").read_text(encoding="utf-8") == "第二版"
    assert (sandbox / "created.txt").exists()

    # simulate a /compact aftermath: the projection replaces the cursor-covered prefix
    with open_session(services, session_id) as session:
        SessionRecorder(session).record_compaction(
            {"role": "user", "content": "[历史摘要] 前两轮"}, keep=2
        )
        assert session.get_value(branch_compaction("main")) is not None

    record = services.runs.start(session_id, "/rewind")
    assert wait_terminal(record), record.status

    assert record.status == "finished"
    finished = next(e for e in collect(services, record.run_id) if e.type == RUN_FINISHED)
    assert finished.data["reason"] == "command"

    # files: the second-round edit is reverted, the second-round new file deleted
    assert (sandbox / "notes.txt").read_text(encoding="utf-8") == "第一版"
    assert not (sandbox / "created.txt").exists()

    # summary: the second question and its 5 descendants move out, 1 file restored, 1 deleted
    assert "移出 5 条" in record.text
    assert "恢复 1 个" in record.text and "删除 1 个" in record.text

    with open_session(services, session_id) as session:
        entries = session.branch("main").find_entries(BranchScan(order="oldestFirst"))
        contents = [str(entry.message.get("content")) for entry in entries if entry.message]
        assert contents[0] == "第一问"
        assert "第二问" not in contents and "第二答" not in contents
        # the summary lands as an assistant entry at the rolled-back tip
        assert entries[-1].message is not None
        assert entries[-1].message["role"] == "assistant"
        assert "已回滚" in contents[-1]
        # compaction cursor cleared: otherwise the projection reattaches the summary
        assert session.get_value(branch_compaction("main")) is None
        # orphaned entries stay on disk (append-only): a global scan still sees them
        every = session.find_entries(EntryQuery(order="asc"))
        assert any(
            entry.message and entry.message.get("content") == "第二问" for entry in every
        )


def test_rewind_command_without_a_user_input_says_so(sandbox):
    """An empty session has no anchor to roll back to: the command still ends with a summary."""
    services = build(sandbox, ScriptedChat())
    record = run_to_end(services, prompt="/rewind")

    assert record.status == "finished"
    assert record.text == "没有可回滚的用户输入"
    finished = next(
        e for e in collect(services, record.run_id) if e.type == RUN_FINISHED
    )
    assert finished.data["reason"] == "command"

