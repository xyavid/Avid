"""条目 → messages 的投影：完整链原样返回，不完整的尾巴截断。

判据是硬的：投影结果必须能通过 ``Transcript`` 的结构校验——否则"续接"
在真实运行里会直接抛错。
"""

from __future__ import annotations

import itertools

from avid.ai.transcript import Transcript
from avid.session import (
    MemorySessionRepo,
    SessionRecorder,
    UuidV7Generator,
    entries_to_messages,
    messages_for_branch,
    repair_incomplete_batches,
)


def clock(start: int = 1_700_000_000_000, step: int = 1_000):
    counter = itertools.count(start, step)
    return lambda: next(counter)


def make_session():
    tick = clock()
    repo = MemorySessionRepo(now=tick, id_generator=UuidV7Generator(tick))
    session = repo.create(id="s")
    return repo, session


def user(text="hi"):
    return {"role": "user", "content": text}


def assistant(text="", calls=()):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = list(calls)
    return message


def call(call_id="c1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "read_file", "arguments": "{}"},
    }


def tool(call_id="c1", content="结果"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


# ---------------- 投影 ----------------


def test_missing_branch_projects_to_nothing():
    _, session = make_session()
    assert messages_for_branch(session) == []
    assert messages_for_branch(session, "never-created") == []
    session.close()


def test_entries_project_in_write_order():
    _, session = make_session()
    recorder = SessionRecorder(session)
    for message in (user("一"), assistant("二")):
        recorder.on_message(message)
    assert messages_for_branch(session) == [user("一"), assistant("二")]
    session.close()


def test_projection_returns_copies():
    _, session = make_session()
    recorder = SessionRecorder(session)
    recorder.on_message(user("原文"))
    projected = messages_for_branch(session)
    projected[0]["content"] = "改过了"
    assert messages_for_branch(session) == [user("原文")]
    session.close()


def test_entries_to_messages_skips_non_message_entries():
    _, session = make_session()
    recorder = SessionRecorder(session)
    recorder.on_message(user("一"))
    entries = session.find_entries()
    assert entries_to_messages(entries) == [user("一")]
    assert entries_to_messages([]) == []
    session.close()


# ---------------- 修复规则 ----------------


def test_complete_batches_pass_through():
    messages = [user(), assistant("", [call()]), tool(), assistant("完")]
    assert repair_incomplete_batches(messages) == messages


def test_trailing_assistant_without_results_is_dropped():
    messages = [user(), assistant("", [call()])]
    assert repair_incomplete_batches(messages) == [user()]


def test_incomplete_batch_in_the_middle_is_removed_and_the_rest_kept():
    # 崩溃之后又续接出来的轮次必须保留：只丢那半截批次。
    messages = [
        user("一"),
        assistant("", [call("c1"), call("c2")]),
        tool("c1"),
        user("续接的问题"),
        assistant("续接的回答"),
    ]
    assert repair_incomplete_batches(messages) == [
        user("一"),
        user("续接的问题"),
        assistant("续接的回答"),
    ]


def test_orphan_tool_results_are_dropped():
    messages = [user(), tool("never-called"), assistant("完")]
    assert repair_incomplete_batches(messages) == [user(), assistant("完")]


def test_complete_chain_after_repair_passes_transcript_validation():
    messages = [user(), assistant("", [call()]), tool(), assistant("完")]
    assert Transcript(repair_incomplete_batches(messages)).validate() == []


def test_crash_tail_is_invisible_after_projection():
    """崩在工具结果之前：会话里留着半截，投影后模型看不到它。"""
    _, session = make_session()
    recorder = SessionRecorder(session)
    recorder.on_message(user("问题"))
    recorder.on_message(assistant("", [call()]))
    assert session.get_stats().message_count == 2

    projected = messages_for_branch(session)
    assert projected == [user("问题")]
    assert Transcript(projected).validate() == []
    session.close()
