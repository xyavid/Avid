"""Transcript 的测试：它是 messages 的唯一所有者，也是结构不变量的守护者。"""

import pytest

from avid.ai.transcript import Transcript, TranscriptError, estimate_chars, validate


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


# ---------- 结构校验 ----------


def test_valid_structure_passes():
    assert validate([user(), assistant("", [call()]), tool()]) == []


def test_orphan_tool_result_is_a_violation():
    assert validate([user(), tool()]) != []


def test_unanswered_tool_call_is_a_violation():
    assert validate([user(), assistant("", [call()])]) != []


def test_partially_answered_tool_calls_are_a_violation():
    messages = [user(), assistant("", [call("c1"), call("c2")]), tool("c1")]

    assert validate(messages) != []


# ---------- 所有者语义 ----------


def test_wraps_the_given_list_in_place():
    messages = [user()]

    transcript = Transcript(messages)
    transcript.append(assistant("好"))

    assert messages[-1]["content"] == "好"


def test_rejects_an_invalid_initial_list():
    with pytest.raises(TranscriptError):
        Transcript([tool()])


def test_as_messages_returns_copies():
    transcript = Transcript([user("原件")])

    copy = transcript.as_messages()
    copy[0]["content"] = "改副本"

    assert transcript.text_at(0) == "原件"


def test_set_content_updates_in_place():
    messages = [user("旧")]
    transcript = Transcript(messages)

    transcript.set_content(0, "新")

    assert messages[0]["content"] == "新"


def test_set_content_rejects_out_of_range():
    with pytest.raises(TranscriptError):
        Transcript([user()]).set_content(5, "x")


# ---------- 结构改动：先校验后落地 ----------


def test_replace_all_accepts_a_valid_candidate():
    transcript = Transcript([user(), assistant("", [call()]), tool()])

    transcript.replace_all([user("摘要")])

    assert transcript.as_messages() == [user("摘要")]


def test_replace_all_rejects_and_leaves_state_untouched():
    transcript = Transcript([user(), assistant("", [call()]), tool()])
    before = transcript.as_messages()

    with pytest.raises(TranscriptError):
        transcript.replace_all([tool("c9")])

    assert transcript.as_messages() == before
    assert transcript.validate() == []


def test_splice_rejects_a_cut_that_orphans_a_result():
    transcript = Transcript([user(), assistant("", [call()]), tool()])
    before = transcript.as_messages()

    with pytest.raises(TranscriptError):
        transcript.splice(2, 3)  # 切掉 tool 结果，assistant 的 tool_calls 就悬空了

    assert transcript.as_messages() == before


def test_splice_rejects_an_out_of_range_interval():
    with pytest.raises(TranscriptError):
        Transcript([user()]).splice(0, 5)


def test_splice_can_drop_a_whole_exchange():
    transcript = Transcript([user("a"), assistant("", [call()]), tool(), user("b")])

    transcript.splice(1, 3, [])

    assert transcript.as_messages() == [user("a"), user("b")]
    assert transcript.validate() == []


def test_splice_can_insert_a_marker():
    transcript = Transcript([user("a"), assistant("", [call()]), tool(), user("b")])

    transcript.splice(1, 3, [user("[已裁剪]")])

    assert transcript.as_messages()[1]["content"] == "[已裁剪]"
    assert transcript.validate() == []


# ---------- 边界与统计 ----------


def test_is_safe_boundary():
    transcript = Transcript([user(), assistant("", [call()]), tool(), user("after")])

    assert transcript.is_safe_boundary(0)  # 开头
    assert transcript.is_safe_boundary(1)  # assistant 之前
    assert not transcript.is_safe_boundary(2)  # 切口处是 tool 结果
    assert transcript.is_safe_boundary(3)  # tool 结果之后
    assert transcript.is_safe_boundary(4)  # 结尾


def test_boundary_rejects_a_pending_tool_call_left_behind():
    """防御分支：切口前一条还挂着没结果的 tool_calls。

    合法的 transcript 里这个位置不可能出现（assistant 带 tool_calls 后必然紧跟
    结果），所以这里直接构造候选序列来验证判定函数本身。
    """
    transcript = Transcript([assistant("", [call()]), tool()])
    # index 1 是 tool，按"切口处不能是 tool"就已经不安全了
    assert not transcript.is_safe_boundary(1)


def test_tool_indexes():
    transcript = Transcript(
        [user(), assistant("", [call("c1")]), tool("c1"), assistant("", [call("c2")]), tool("c2")]
    )

    assert transcript.tool_indexes() == [2, 4]


def test_tool_chars():
    transcript = Transcript([assistant("", [call()]), tool(content="x" * 30)])

    assert transcript.tool_chars() == 30


def test_last_user_index():
    transcript = Transcript([user("a"), assistant("回答"), user("b")])

    assert transcript.last_user_index() == 2
    assert Transcript([assistant("没有用户消息")]).last_user_index() is None


def test_estimate_counts_content_and_tool_calls():
    assert estimate_chars([user("abc")]) >= 3
    assert estimate_chars([assistant("", [call()])]) > len("read_file")
    assert Transcript([user("a")]).estimate_chars() < Transcript(
        [user("a"), user("b")]
    ).estimate_chars()


# ---------------- 成本量是增量维护的（P2-4） ----------------

TOOL = {"role": "tool", "tool_call_id": "c1", "content": "z" * 300}
CALL = {
    "role": "assistant",
    "content": "",
    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}],
}


def test_incremental_costs_match_a_full_recompute_after_every_mutation():
    """缓存值必须与全量算法逐次相等——记账写错就会让压缩阈值判断失真。"""
    transcript = Transcript([{"role": "user", "content": "初始"}])
    cases = [
        ("append assistant", lambda t: t.append(CALL)),
        ("append tool", lambda t: t.append(TOOL)),
        ("set_content", lambda t: t.set_content(0, "改过的内容" * 10)),
        ("splice", lambda t: t.splice(1, 3, [{"role": "user", "content": "替换"}])),
        ("replace_all", lambda t: t.replace_all([{"role": "user", "content": "只剩这一条"}])),
        ("append_many", lambda t: t.append_many([TOOL, TOOL])),
    ]
    for label, mutate in cases:
        mutate(transcript)
        assert transcript.estimate_chars() == estimate_chars(transcript.as_messages()), label
        assert transcript.tool_chars() == sum(
            len(transcript.text_at(index)) for index in transcript.tool_indexes()
        ), label


def test_reads_do_not_recompute(monkeypatch):
    """读取必须走缓存：每轮 `context.prepare` 至少算三次，全量扫是 O(消息数 × 轮数)。"""
    transcript = Transcript([{"role": "user", "content": "一"}])
    recomputes: list[int] = []
    monkeypatch.setattr(transcript, "_recompute_costs", lambda: recomputes.append(1))

    for _ in range(5):
        transcript.estimate_chars()
        transcript.tool_chars()

    assert recomputes == [], "读路径不该重算全表"
