"""条目 → messages 的投影：完整链原样返回；没等到结果的工具批补上「结果未落盘」的合成结果（只存在于投影返回值，不落库）。

判据是硬的：投影结果必须能通过 ``Transcript`` 的结构校验——否则"续接"
在真实运行里会直接抛错。
"""

from __future__ import annotations

import itertools

from avid.agent.transcript import Transcript
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


# 与实现的合成文案逐字一致：恢复语义是契约，内容漂移必须被这里拦住。
CUT_OFF = (
    "（此调用的结果没有落盘：运行在结果记录前被切断，执行状态未知，"
    "可能已生效。请先核实实际状态（读文件/查状态）再决定是否重做。）"
)


# ---------------- 压缩游标（诊断 C2：splice 只改内存，投影时应用） ----------------


def test_compaction_record_replaces_covered_prefix_with_summary():
    repo, session = make_session()
    recorder = SessionRecorder(session)
    for i in range(10):
        recorder.on_message(user(f"m{i}"))
    summary = {"role": "user", "content": "[历史摘要] 之前的对话要点"}

    recorder.record_compaction(summary, keep=0)

    assert messages_for_branch(session) == [summary]


def test_compaction_record_keeps_the_requested_tail():
    repo, session = make_session()
    recorder = SessionRecorder(session)
    for i in range(10):
        recorder.on_message(user(f"m{i}"))

    recorder.record_compaction({"role": "user", "content": "[历史摘要]"}, keep=3)

    messages = messages_for_branch(session)
    assert messages[0]["content"] == "[历史摘要]"
    assert [m["content"] for m in messages[1:]] == ["m7", "m8", "m9"]


def test_messages_after_the_cursor_project_normally():
    repo, session = make_session()
    recorder = SessionRecorder(session)
    for i in range(10):
        recorder.on_message(user(f"m{i}"))
    recorder.record_compaction({"role": "user", "content": "[历史摘要]"}, keep=0)
    recorder.on_message(user("压缩之后的新消息"))

    messages = messages_for_branch(session)
    assert [m["content"] for m in messages] == ["[历史摘要]", "压缩之后的新消息"]


def test_projection_without_a_record_is_unchanged():
    repo, session = make_session()
    recorder = SessionRecorder(session)
    for i in range(3):
        recorder.on_message(user(f"m{i}"))

    assert [m["content"] for m in messages_for_branch(session)] == ["m0", "m1", "m2"]


def test_a_second_compaction_record_overwrites_the_first():
    repo, session = make_session()
    recorder = SessionRecorder(session)
    for i in range(10):
        recorder.on_message(user(f"m{i}"))
    recorder.record_compaction({"role": "user", "content": "[历史摘要] 一"}, keep=0)
    recorder.on_message(user("又干了点活"))
    recorder.record_compaction({"role": "user", "content": "[历史摘要] 二"}, keep=1)

    messages = messages_for_branch(session)
    assert [m["content"] for m in messages] == ["[历史摘要] 二", "又干了点活"]


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


def test_trailing_incomplete_batch_gets_synthesized_results():
    # 链尾未完成批：assistant 保留，每个缺失 call 按原顺序补一条合成结果。
    messages = [user(), assistant("", [call("c2"), call("c1")])]
    projected = repair_incomplete_batches(messages)
    assert projected == [
        user(),
        assistant("", [call("c2"), call("c1")]),
        tool("c2", CUT_OFF),
        tool("c1", CUT_OFF),
    ]


def test_incomplete_batch_in_the_middle_is_completed_and_the_rest_kept():
    # 崩溃之后又续接出来的轮次必须保留：半截批补全（缺的补合成结果），后面照常。
    messages = [
        user("一"),
        assistant("", [call("c1"), call("c2")]),
        tool("c1"),
        user("续接的问题"),
        assistant("续接的回答"),
    ]
    assert repair_incomplete_batches(messages) == [
        user("一"),
        assistant("", [call("c1"), call("c2")]),
        tool("c1"),
        tool("c2", CUT_OFF),
        user("续接的问题"),
        assistant("续接的回答"),
    ]


def test_partial_results_are_kept_verbatim_and_missing_ones_synthesized():
    messages = [user(), assistant("", [call("c1"), call("c2")]), tool("c1", "已到的结果")]
    projected = repair_incomplete_batches(messages)
    assert projected == [
        user(),
        assistant("", [call("c1"), call("c2")]),
        tool("c1", "已到的结果"),
        tool("c2", CUT_OFF),
    ]
    # 合成结果只追加在返回值里，入参列表不动：落库由 recorder 决定，投影永不写。
    assert len(messages) == 3


def test_orphan_tool_results_are_dropped():
    messages = [user(), tool("never-called"), assistant("完")]
    assert repair_incomplete_batches(messages) == [user(), assistant("完")]


def test_complete_chain_after_repair_passes_transcript_validation():
    messages = [user(), assistant("", [call()]), tool(), assistant("完")]
    assert Transcript(repair_incomplete_batches(messages)).validate() == []


def test_chain_completed_with_synthesized_results_passes_transcript_validation():
    messages = [user(), assistant("", [call("c1"), call("c2")]), tool("c1"), user("后续")]
    repaired = repair_incomplete_batches(messages)
    assert Transcript(repaired).validate() == []


def test_crash_tail_is_completed_after_projection():
    """崩在工具结果之前：投影补上「结果未落盘」的合成结果，模型先核实再决定是否重做。"""
    _, session = make_session()
    recorder = SessionRecorder(session)
    recorder.on_message(user("问题"))
    recorder.on_message(assistant("", [call()]))
    assert session.get_stats().message_count == 2

    projected = messages_for_branch(session)
    assert projected == [user("问题"), assistant("", [call()]), tool("c1", CUT_OFF)]
    assert Transcript(projected).validate() == []
    # 合成结果不落库：条目数不变
    assert session.get_stats().message_count == 2
    session.close()
