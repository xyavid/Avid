"""会话基础件：id、值地址、物化状态、变更线与关闭语义。

一致性套件（``test_session_conformance.py``）管"两个后端是否一致"；这里管
"单个部件本身的规则"，包括两个后端都跑不到的多线程关闭路径。
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid

import pytest

from avid.session import (
    BranchScan,
    CommittedEntry,
    CommittedValueSet,
    Entry,
    EntryWrite,
    MutationLine,
    NewEntry,
    SessionBusyError,
    SessionClosedError,
    SessionInvalidMessageError,
    SessionInvariantError,
    UuidV7Generator,
    new_uuidv7,
    now_ms,
    set_value,
    validate_message,
    validate_session_id,
    value,
)
from avid.session.memory import MemorySessionRepo
from avid.session.state import SessionState


def clock(start: int = 1_700_000_000_000, step: int = 1_000):
    counter = itertools.count(start, step)
    return lambda: next(counter)


def make_session(**kwargs):
    tick = clock()
    repo = MemorySessionRepo(now=tick, id_generator=UuidV7Generator(tick))
    session = repo.create(id="s")
    session.create_branch("main", None)
    return repo, session


# ---------------- id ----------------


def test_new_id_is_a_uuidv7_with_the_given_time():
    parsed = uuid.UUID(new_uuidv7(1_700_000_000_000))
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert parsed.int >> 80 == 1_700_000_000_000


def test_new_ids_are_time_ordered_and_unique():
    early = new_uuidv7(1_700_000_000_000)
    late = new_uuidv7(1_700_000_001_000)
    assert early < late
    assert len({new_uuidv7(1_700_000_000_000) for _ in range(50)}) == 50


def test_generator_uses_the_injected_clock():
    tick = clock(start=1, step=1)
    generator = UuidV7Generator(tick)
    first, second = generator.next(), generator.next()
    assert uuid.UUID(first).int >> 80 == 1
    assert uuid.UUID(second).int >> 80 == 2
    assert first < second


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", "a\u0000b", 42, None])
def test_invalid_session_ids_are_rejected(bad):
    with pytest.raises(Exception) as info:
        validate_session_id(bad)
    assert info.type.__name__ == "SessionInvalidIdError"


def test_now_ms_is_an_integer_millisecond_clock():
    assert isinstance(now_ms(), int)
    assert now_ms() > 1_600_000_000_000


# ---------------- 值地址 ----------------


def test_address_validation():
    with pytest.raises(TypeError):
        value("")
    with pytest.raises(TypeError):
        value("ns\u0000me")
    with pytest.raises(TypeError):
        value("ns", "k\u0000ey")
    assert value("ns").key == ""
    assert value("ns", "k").namespace == "ns"


def test_reserved_addresses_are_distinct_namespaces():
    from avid.session import branch_tip, entry_label, session_name

    assert session_name().namespace == "avid.session.name"
    assert entry_label("e1").key == "e1"
    assert branch_tip("main").key == "main"
    assert len({session_name().namespace, entry_label("e").namespace, branch_tip("b").namespace}) == 3


# ---------------- 消息校验 ----------------


def test_valid_message_shapes_pass():
    validate_message({"role": "user", "content": "hi"})
    validate_message({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
    ]})
    validate_message({"role": "tool", "tool_call_id": "c1", "content": "结果"})


@pytest.mark.parametrize(
    "message",
    [
        "文本",
        None,
        {"content": "没有 role"},
        {"role": "system", "content": "x"},
        {"role": "tool", "content": "缺 tool_call_id"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x"}}]},
    ],
)
def test_invalid_message_shapes_are_rejected(message):
    with pytest.raises(SessionInvalidMessageError):
        validate_message(message)


def test_non_serializable_message_is_rejected():
    with pytest.raises(SessionInvalidMessageError):
        validate_message({"role": "user", "content": object()})


# ---------------- 物化状态 ----------------


def test_commit_assigns_continuous_seqs_and_one_timestamp():
    state = SessionState()
    first = EntryWrite(NewEntry(id="a", parent_id=None, message={"role": "user", "content": "1"}))
    second = EntryWrite(NewEntry(id="b", parent_id="a", message={"role": "user", "content": "2"}))
    prepared = state.prepare_commit([first, set_value(value("ns"), 1), second], 42)
    assert prepared.seqs == (1, 2, 3)
    assert prepared.first_seq == 1
    assert prepared.timestamp == 42
    assert all(item.timestamp == 42 for item in prepared.writes if isinstance(item, CommittedEntry))
    assert state.next_seq == 1  # prepare 不改动状态
    stats = state.apply(prepared.writes)
    assert stats.message_count == 2
    assert state.next_seq == 4
    assert isinstance(prepared.writes[1], CommittedValueSet)


def test_validate_rejects_duplicate_id_missing_parent_and_unsorted_seq():
    state = SessionState()
    state.apply(state.prepare_commit([EntryWrite(NewEntry(id="a", parent_id=None, message={"role": "user", "content": "1"}))], 1).writes)

    duplicate = [CommittedEntry(2, 1, NewEntry(id="a", parent_id=None))]
    with pytest.raises(SessionInvariantError):
        state.validate(duplicate)

    orphan = [CommittedEntry(2, 1, NewEntry(id="b", parent_id="missing"))]
    with pytest.raises(SessionInvariantError):
        state.validate(orphan)

    unsorted = [
        CommittedEntry(5, 1, NewEntry(id="b", parent_id="a")),
        CommittedEntry(5, 1, NewEntry(id="c", parent_id="a")),
    ]
    with pytest.raises(SessionInvariantError):
        state.validate(unsorted)


def test_advance_next_seq_only_moves_forward():
    state = SessionState()
    state.advance_next_seq(10)
    assert state.next_seq == 10
    state.advance_next_seq(3)
    assert state.next_seq == 10
    with pytest.raises(SessionInvariantError):
        state.advance_next_seq(0)


def test_scan_branch_needs_a_start_and_a_complete_chain():
    state = SessionState()
    with pytest.raises(SessionInvariantError):
        state.scan_branch(BranchScan())
    with pytest.raises(SessionInvariantError):
        state.scan_branch(BranchScan(start="missing"))

    # 人为造一条断链：apply 时父条目必须已存在，所以这里直接改内部结构来模拟损坏。
    state.apply(state.prepare_commit([EntryWrite(NewEntry(id="a", parent_id=None, message={"role": "user", "content": "1"}))], 1).writes)
    state._entries["a"] = Entry(id="a", parent_id="ghost", seq=1, timestamp=1, type="message", message={"role": "user", "content": "1"})
    with pytest.raises(SessionInvariantError):
        state.scan_branch(BranchScan(start="a"))


# ---------------- 变更线 ----------------


def test_sealed_line_refuses_new_acquires():
    line = MutationLine()
    error = SessionClosedError("关了")
    line.seal(error)
    assert line.sealed_error is error
    with pytest.raises(SessionClosedError):
        line.acquire()


def test_nested_acquire_on_same_thread_reports_busy():
    line = MutationLine()
    line.acquire()
    with pytest.raises(SessionBusyError):
        line.acquire()
    line.release()
    line.acquire()
    line.release()


def test_wait_idle_returns_only_after_release():
    line = MutationLine()
    line.acquire()
    done = threading.Event()

    def waiter():
        line.wait_idle()
        done.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.05)
    assert not done.is_set()
    line.release()
    thread.join(5)
    assert done.is_set()


# ---------------- 关闭语义（线程级） ----------------


def test_close_waits_for_another_threads_mutation_and_keeps_its_commit():
    repo, session = make_session()
    started = threading.Event()
    release = threading.Event()
    hold = value("test.app.held")

    def worker():
        mutator = session.begin_mutation()
        started.set()
        release.wait(5)
        mutator.commit([set_value(hold, 1)])
        mutator.end()

    worker_thread = threading.Thread(target=worker)
    worker_thread.start()
    assert started.wait(5)

    closed = threading.Event()

    def closer():
        session.close()
        closed.set()

    closer_thread = threading.Thread(target=closer)
    closer_thread.start()
    time.sleep(0.05)
    assert not closed.is_set()  # close 在等正在跑的作业

    release.set()
    worker_thread.join(5)
    closer_thread.join(5)
    assert closed.is_set()
    assert session.closed is True

    reopened = repo.open(session.metadata)
    assert reopened.get_value(hold).value == 1
    reopened.close()


def test_waiting_mutation_is_rejected_once_close_seals():
    repo, session = make_session()
    holder = session.begin_mutation()
    errors: list[Exception] = []

    def waiter():
        try:
            session.begin_mutation()
        except Exception as exc:  # noqa: BLE001 - 这里就是要看它抛了什么
            errors.append(exc)

    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    time.sleep(0.05)

    closer_thread = threading.Thread(target=session.close)
    closer_thread.start()
    time.sleep(0.05)
    holder.end()
    waiter_thread.join(5)
    closer_thread.join(5)

    assert errors and isinstance(errors[0], SessionClosedError)
    assert session.closed is True
