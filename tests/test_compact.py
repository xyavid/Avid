import inspect

import pytest

from avid.ai.client import Turn, Usage
from avid.ai.config import Config
from avid.ai.transcript import Transcript, estimate_chars, validate
from avid.policy.compaction import (
    SPILL_PREFIX,
    compact_history,
    micro_compact,
    reactive_compact,
    snip_compact,
    tool_result_budget,
)

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


@pytest.fixture(autouse=True)
def spill_root(tmp_path, monkeypatch):
    """落盘写到临时工作区，测试不污染仓库。"""
    from avid.tools import workspace

    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path)
    return tmp_path


class FakeChat:
    def __init__(self, text="摘要正文"):
        self.text = text
        self.requests = []

    def __call__(self, config, messages, **kwargs):
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        return Turn(
            message={"role": "assistant", "content": self.text},
            text=self.text,
            tool_calls=[],
            usage=Usage(1, 1, 2),
            model="m",
            finish_reason="stop",
        )


def user(text="hi"):
    return {"role": "user", "content": text}


def assistant(text="", calls=()):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = list(calls)
    return message


def call(call_id="c1", name="read_file"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def tool(call_id="c1", content="结果"):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def tool_chars(messages):
    return sum(len(m["content"]) for m in messages if m.get("role") == "tool")


# ---------- 基础工具 ----------


def test_estimate_counts_content_and_tool_calls():
    plain = [user("abc")]
    with_calls = [assistant("", [call()])]

    assert estimate_chars(plain) >= 3
    assert estimate_chars(with_calls) > len("read_file")


def test_estimate_grows_with_messages():
    assert estimate_chars([user("a")]) < estimate_chars([user("a"), user("b")])


def test_valid_structure_passes():
    assert validate([user(), assistant("", [call()]), tool()]) == []


def test_orphan_tool_result_is_a_violation():
    assert validate([user(), tool()]) != []


def test_unanswered_tool_call_is_a_violation():
    assert validate([user(), assistant("", [call()])]) != []


def test_partially_answered_tool_calls_are_a_violation():
    messages = [user(), assistant("", [call("c1"), call("c2")]), tool("c1")]

    assert validate(messages) != []


# ---------- ① tool_result_budget ----------


def test_budget_is_a_no_op_under_limit():
    messages = [user(), assistant("", [call()]), tool(content="x" * 10)]

    assert tool_result_budget(Transcript(messages), budget=100) is None
    assert messages[2]["content"] == "x" * 10


def test_budget_spills_the_largest_among_the_older_ones(spill_root):
    messages = [user()]
    for index, size in enumerate([100, 500, 50, 60, 10]):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * size))

    report = tool_result_budget(Transcript(messages), budget=50, keep_recent=3)

    assert report is not None
    assert report.step == "tool_result_budget"
    # c1（500 字符）是可落盘项里最大的
    assert messages[4]["content"].startswith(SPILL_PREFIX)
    assert "500" in messages[4]["content"]

    path = messages[4]["content"].split("已存至 ")[1].split("；")[0]
    assert (spill_root / path).read_text(encoding="utf-8") == "x" * 500


def test_spill_names_are_unique_per_run_and_never_reused(spill_root):
    """落盘文件名必须带运行标识。

    只用进程内自增序号时，`_spill_seq` 重启归零 → 同一个工作区里两次运行都写
    `tool-result-0001.txt`，后一次静默覆盖前一次；而旧摘要里还写着"完整记录：
    …-0001.txt，需要时用 read_file 读回"，那条恢复通道就断了。
    """
    messages = [user()]
    for index, size in enumerate([100, 500, 50, 60, 10]):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * size))
    transcript = Transcript(messages)

    tool_result_budget(transcript, budget=50, keep_recent=3, tag="runAAAAA")
    first = messages[4]["content"].split("已存至 ")[1].split("；")[0]
    assert "runAAAAA" in first

    # 同一份对话再来一次（另一个 run_tag，且序号从同一个进程内计数器继续）：
    messages2 = [user()]
    for index, size in enumerate([100, 500, 50, 60, 10]):
        messages2.append(assistant("", [call(f"c{index}")]))
        messages2.append(tool(f"c{index}", "x" * size))
    tool_result_budget(Transcript(messages2), budget=50, keep_recent=3, tag="runBBBBB")
    second = messages2[4]["content"].split("已存至 ")[1].split("；")[0]

    assert first != second
    assert (spill_root / first).read_text(encoding="utf-8") == "x" * 500
    assert (spill_root / second).read_text(encoding="utf-8") == "x" * 500


def test_concurrent_spills_produce_distinct_files(spill_root):
    """并行 subagent 会同时压缩：序号必须原子地取，不能两个线程拿到同一个名字。"""
    import threading

    from avid.policy.compaction import _spill

    paths: list[str] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        path = _spill(f"payload-{index}", "tool-result", spill_root, "shared1")
        assert path is not None
        with lock:
            paths.append(path)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(paths) == 32
    assert len(set(paths)) == 32, "序号竞态会让两个线程写同一个文件"
    assert len(list((spill_root / ".avid" / "context").glob("*.txt"))) == 32


def test_budget_never_spills_the_newest_results(spill_root):
    """模型刚读到的结果正是下一步要用的，落掉它会让模型重读，然后又被落掉。"""
    messages = [user()]
    for index in range(5):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 500))

    tool_result_budget(Transcript(messages), budget=10, keep_recent=3)

    tool_messages = [m for m in messages if m["role"] == "tool"]
    flags = [m["content"].startswith(SPILL_PREFIX) for m in tool_messages]

    assert flags[-3:] == [False, False, False]  # 最近 3 条一条都不许动
    assert any(flags[:-3])


def test_budget_is_a_no_op_when_there_is_nothing_spillable(spill_root):
    """结果条数不超过保留数量时，一条都不该落。"""
    messages = [user()]
    for index in range(3):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 10_000))

    assert tool_result_budget(Transcript(messages), budget=10, keep_recent=3) is None
    assert all(
        not m["content"].startswith(SPILL_PREFIX)
        for m in messages
        if m["role"] == "tool"
    )


def test_budget_spills_at_most_one_per_call(spill_root):
    """一轮剥掉一批会让模型丢掉刚建立的工作集，所以每次只落一项。"""
    messages = [user()]
    for index in range(6):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 1000))

    report = tool_result_budget(Transcript(messages), budget=10, keep_recent=3)

    assert report is not None
    older = [m for m in messages if m["role"] == "tool"][:-3]
    assert sum(m["content"].startswith(SPILL_PREFIX) for m in older) == 1


def test_repeated_budget_calls_drain_older_results_then_stop(spill_root):
    messages = [user()]
    for index in range(6):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 1000))

    for _ in range(10):
        if tool_result_budget(Transcript(messages), budget=10, keep_recent=3) is None:
            break

    older = [m for m in messages if m["role"] == "tool"][:-3]
    assert all(m["content"].startswith(SPILL_PREFIX) for m in older)
    assert tool_result_budget(Transcript(messages), budget=10, keep_recent=3) is None
    assert validate(messages) == []


def test_budget_skips_already_spilled_items():
    messages = [
        user(),
        assistant("", [call()]),
        tool(content=SPILL_PREFIX + " 原工具结果共 999 字符，已存至 .avid/context/x.txt"),
    ]

    assert tool_result_budget(Transcript(messages), budget=10) is None


def test_budget_ignores_non_tool_messages():
    assert tool_result_budget(Transcript([user("x" * 1000)]), budget=10) is None


# ---------- ② snip_compact ----------


def test_snip_is_a_no_op_below_the_limit():
    messages = [user(f"m{i}") for i in range(10)]

    assert snip_compact(Transcript(messages)) is None
    assert len(messages) == 10


def test_snip_keeps_head_and_tail():
    messages = [user(f"m{i}") for i in range(60)]

    report = snip_compact(Transcript(messages))

    assert report is not None
    assert report.step == "snip_compact"
    assert messages[0]["content"] == "m0"
    assert messages[-1]["content"] == "m59"
    assert len(messages) < 60
    assert any("已裁剪" in str(m.get("content")) for m in messages)


def test_snip_never_splits_a_tool_pair():
    messages = [user("开始")]
    for index in range(30):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 5))

    assert len(messages) == 61
    report = snip_compact(Transcript(messages))

    assert report is not None
    assert validate(messages) == []

    kept_results = {m["tool_call_id"] for m in messages if m.get("role") == "tool"}
    declared = {
        c["id"] for m in messages for c in (m.get("tool_calls") or [])
    }
    assert kept_results <= declared


def test_snip_gives_up_when_head_and_tail_cover_everything():
    """头尾保留量之和超过消息数时无中间可裁——放弃而不是硬裁。"""
    messages = [user(f"m{i}") for i in range(11)]

    assert (
        snip_compact(Transcript(messages), max_messages=10, keep_head=8, keep_tail=24)
        is None
    )
    assert len(messages) == 11


# ---------- ③ micro_compact ----------


def test_micro_is_a_no_op_under_the_limit():
    messages = [user(), assistant("", [call()]), tool(content="x" * 100)]

    assert micro_compact(Transcript(messages), limit=10_000) is None


def test_micro_spills_older_results_and_keeps_the_newest(spill_root):
    messages = [user()]
    for index in range(6):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 200))

    report = micro_compact(Transcript(messages), limit=500, keep_recent=3)

    assert report is not None
    assert report.step == "micro_compact"

    tool_messages = [m for m in messages if m["role"] == "tool"]
    flags = [m["content"].startswith(SPILL_PREFIX) for m in tool_messages]

    assert flags[-3:] == [False, False, False]  # 最近 3 条原样保留
    assert any(flags[:-3])
    assert validate(messages) == []


def test_micro_reaches_the_target_when_it_can(spill_root):
    messages = [user()]
    for index in range(2):  # 两条很大的旧结果
        messages.append(assistant("", [call(f"big{index}")]))
        messages.append(tool(f"big{index}", "x" * 20000))
    for index in range(3):  # 三条很小的新结果，会被保留
        messages.append(assistant("", [call(f"small{index}")]))
        messages.append(tool(f"small{index}", "x" * 10))

    report = micro_compact(Transcript(messages), limit=30_000, keep_recent=3)

    assert report is not None
    assert report.after <= int(30_000 * 0.8)


def test_micro_compacts_as_far_as_it_can(spill_root):
    """压到目标为止；若落盘提示本身就把预算占满，压无可压就停手。

    这里"保留最近 3 条 × 1000 字符 + 结构开销"已经超过目标，所以断言的是
    「该压的都压了」，而不是「一定达标」——达标在这个输入下物理上做不到。
    """
    messages = [user()]
    for index in range(10):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", "x" * 1000))

    report = micro_compact(Transcript(messages), limit=5000, keep_recent=3)

    assert report is not None
    assert report.after < report.before
    older = [m for m in messages if m["role"] == "tool"][:-3]
    assert all(m["content"].startswith(SPILL_PREFIX) for m in older)


@pytest.mark.parametrize(
    "step", [tool_result_budget, snip_compact, micro_compact], ids=lambda s: s.__name__
)
def test_cheap_steps_take_no_chat_argument(step):
    """①②③ 不调用模型——连 chat 参数都不该有。"""
    assert "chat" not in inspect.signature(step).parameters


# ---------- ④ compact_history ----------


def test_compact_history_is_a_no_op_under_the_limit():
    chat = FakeChat()
    messages = [user("x" * 100)]

    assert compact_history(Transcript(messages), config=CONFIG, chat=chat, limit=10_000) is None
    assert chat.requests == []


def test_compact_history_summarises_and_replaces(spill_root):
    chat = FakeChat("这是摘要")
    messages = [user("x" * 2000), assistant("y" * 2000)]

    report = compact_history(Transcript(messages), config=CONFIG, chat=chat, limit=100)

    assert report is not None
    assert len(chat.requests) == 1  # 只有一次模型调用
    assert len(messages) == 1
    assert "[历史摘要]" in messages[0]["content"]
    assert "这是摘要" in messages[0]["content"]
    assert ".avid/context/transcript-" in messages[0]["content"]
    assert validate(messages) == []


def test_compact_history_saves_the_full_transcript(spill_root):
    chat = FakeChat("摘要")
    messages = [user("原始内容" * 100)]

    compact_history(Transcript(messages), config=CONFIG, chat=chat, limit=100)

    transcripts = list((spill_root / ".avid/context").glob("transcript-*.json"))
    assert len(transcripts) == 1
    assert "原始内容" in transcripts[0].read_text(encoding="utf-8")


def test_compact_history_keeps_history_when_the_summary_fails(spill_root):
    from avid.ai.client import LLMError

    def broken_chat(*args, **kwargs):
        raise LLMError("摘要服务挂了")

    messages = [user("x" * 2000)]

    assert compact_history(Transcript(messages), config=CONFIG, chat=broken_chat, limit=100) is None
    assert len(messages) == 1
    assert messages[0]["content"].startswith("x")


# ---------- ⑤ reactive_compact ----------


def test_reactive_keeps_the_recent_messages(spill_root):
    chat = FakeChat("早前的摘要")
    messages = [
        user("最早的"),
        assistant("回复"),
        user("中间的"),
        assistant("回复"),
        user("最近的1"),
        assistant("最近的2"),
    ]

    report = reactive_compact(Transcript(messages), config=CONFIG, chat=chat, keep_recent=3)

    assert report is not None
    assert report.step == "reactive_compact"
    assert "[历史摘要]" in messages[0]["content"]
    assert len(messages) == 4
    assert messages[-1]["content"] == "最近的2"


def test_reactive_widens_the_tail_to_keep_a_pair(spill_root):
    chat = FakeChat("摘要")
    messages = [
        user("开始"),
        assistant("", [call("c1")]),
        tool("c1", "结果"),
        assistant("", [call("c2")]),
        tool("c2", "结果2"),
    ]

    report = reactive_compact(Transcript(messages), config=CONFIG, chat=chat, keep_recent=1)

    assert report is not None
    assert validate(messages) == []
    assert messages[-2]["tool_calls"][0]["id"] == "c2"
    assert messages[-1]["tool_call_id"] == "c2"


def test_reactive_gives_up_with_nothing_earlier():
    chat = FakeChat()
    messages = [user("只有这一条")]

    assert reactive_compact(Transcript(messages), config=CONFIG, chat=chat, keep_recent=5) is None
    assert chat.requests == []
