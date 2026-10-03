"""压缩子系统测试：触发线（reserve 语义）、切点（安全边界）、单一压缩通路。

修复前这里是五步阶梯（tool_result_budget / snip / micro / compact_history /
reactive）各自的用例；按用户裁定收敛为一条 Pi 式通路——保留最近 N 轮完整
历史，更早部分经固定 prompt 一次 summarize，切点绝不落在工具批中间。
"""


import pytest

from avid.agent.compaction import (
    CONTEXT_CHAR_LIMIT,
    CompactReport,
    ContextBudget,
    _next_spill_path,
    _summarize,
    _summary_message,
    announce,
    cut_point,
    run_compaction,
    spill,
    trigger_chars,
)
from avid.agent.state import RunState
from avid.agent.transcript import Transcript, estimate_chars, validate
from avid.providers.client import LLMError, Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


@pytest.fixture(autouse=True)
def spill_root(tmp_path, monkeypatch):
    """落盘写到临时工作区，测试不污染仓库。"""
    from avid.agent.tools import workspace

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


def rounds(count, content="x" * 200):
    """构造 count 轮：每轮一条 assistant（带工具调用）加一条工具结果。"""
    messages = [user("任务")]
    for index in range(count):
        messages.append(assistant("", [call(f"c{index}")]))
        messages.append(tool(f"c{index}", content))
    return messages


# ---------- Transcript 基础（estimate / validate） ----------


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


# ---------- 落盘通道 ----------


def test_spill_names_are_unique_per_tag_and_sequence(spill_root):
    first = _next_spill_path(spill_root, "transcript", ".json", tag="runAAAAA")
    second = _next_spill_path(spill_root, "transcript", ".json", tag="runAAAAA")
    other = _next_spill_path(spill_root, "transcript", ".json", tag="runBBBBB")

    assert first != second
    assert "runAAAAA" in str(first) and "runBBBBB" in str(other)


def test_spill_writes_and_returns_workspace_relative_path(spill_root):
    path = spill("x" * 500, "transcript")

    assert path is not None and path.startswith(".avid/context/")
    assert (spill_root / path).read_text(encoding="utf-8") == "x" * 500


# ---------- 触发线（reserve 语义） ----------


def test_trigger_is_window_minus_reserve_times_measured_rate():
    # window 200k，reserve 16384，实测 0.515 字符/token，system+tools 1500 字符
    trigger = trigger_chars(
        window=200_000,
        prompt_tokens=100_000,
        chars=(1000, 500, 50_000),
        reserve_tokens=16_384,
    )
    per_token = 0.515  # 51_500 / 100_000
    assert trigger == int((200_000 - 16_384) * per_token) - 1500


def test_trigger_without_a_reading_uses_the_default_rate():
    """有窗口但没有读数：按 2.0 字符/token 的保守默认比率折算，而不是整个放弃派生。"""
    trigger = trigger_chars(window=200_000, prompt_tokens=None, chars=None, reserve_tokens=16_384)
    assert trigger == (200_000 - 16_384) * 2


def test_trigger_falls_back_without_a_window():
    assert trigger_chars(window=None, prompt_tokens=None, chars=None, reserve_tokens=16_384) == CONTEXT_CHAR_LIMIT


def test_trigger_scales_with_reserve():
    small = trigger_chars(
        window=200_000, prompt_tokens=100_000, chars=(1000, 500, 50_000), reserve_tokens=16_384
    )
    big = trigger_chars(
        window=200_000, prompt_tokens=100_000, chars=(1000, 500, 50_000), reserve_tokens=1_000
    )
    assert big > small  # reserve 越大，留给历史的空间越小


# ---------- 切点 ----------


def test_cut_point_walks_back_full_rounds():
    messages = rounds(12)
    transcript = Transcript(messages)

    cut = cut_point(transcript, keep_recent_turns=10)

    # 切点前是任务消息 + 前 2 轮；切点后保留最近 10 轮（20 条消息）
    assert messages[cut - 1]["role"] == "tool"
    assert messages[cut]["role"] == "assistant"
    assert len(messages) - cut == 20


def test_cut_point_never_splits_a_tool_pair():
    messages = rounds(12)
    transcript = Transcript(messages)

    cut = cut_point(transcript, keep_recent_turns=10)

    assert validate(messages[cut:]) == []


def test_cut_point_returns_zero_when_fewer_rounds_than_the_window():
    transcript = Transcript(rounds(5))

    assert cut_point(transcript, keep_recent_turns=10) == 0


# ---------- 压缩通路 ----------


def budget(**overrides):
    defaults = {"keep_recent_turns": 10, "context_chars": 10, "from_window": False}
    defaults.update(overrides)
    return ContextBudget(**defaults)


def test_below_trigger_nothing_happens():
    transcript = Transcript(rounds(12))
    chat = FakeChat()

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(context_chars=10_000_000),
        force=False,
    )

    assert report is None
    assert chat.requests == []
    assert len(transcript) == len(rounds(12))


def test_above_trigger_summarizes_older_history_and_keeps_recent_turns():
    transcript = Transcript(rounds(12))
    before_chars = transcript.estimate_chars()
    chat = FakeChat("之前做了 A，结论 B")

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=chat,
        limits=budget(),
        force=False,
    )

    assert report is not None
    messages = transcript.as_messages()
    assert len(messages) == 1 + 20  # 摘要一条 + 最近 10 轮
    assert messages[0]["content"].startswith("[历史摘要]")
    assert validate(messages) == []
    # 固定 prompt 的摘要请求里带上了被压缩的更早历史
    assert chat.requests[0]["system"].startswith("你是上下文压缩器")
    assert transcript.estimate_chars() < before_chars


def test_summarized_history_is_still_a_valid_transcript():
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
    )

    assert validate(transcript.as_messages()) == []


def test_summarize_failure_keeps_history_and_reports_nothing():
    class FailingChat:
        def __call__(self, config, messages, **kwargs):
            raise LLMError("端点挂了")

    transcript = Transcript(rounds(12))

    report = run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FailingChat(),
        limits=budget(),
    )

    assert report is None
    assert transcript.as_messages() == rounds(12)


def test_full_record_is_saved_for_recovery(spill_root):
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
    )

    summary_message = transcript.as_messages()[0]["content"]
    recorded = summary_message.split("完整记录：")[1].split("；")[0]
    assert (spill_root / recorded).exists()


def test_auto_path_compacts_at_most_once_per_run():
    transcript = Transcript(rounds(12))
    state = RunState(workspace_root=str(spill_root))
    chat = FakeChat()

    first = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget()
    )
    second = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget()
    )

    assert first is not None
    assert second is None  # 每运行一次：防摘要失败后的逐轮重试风暴
    assert state.compacted is True


def test_force_bypasses_threshold_and_the_once_guard():
    transcript = Transcript(rounds(12))
    state = RunState(workspace_root=str(spill_root))
    chat = FakeChat()

    first = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget(), force=True
    )
    second = run_compaction(
        transcript=transcript, state=state, config=CONFIG, chat=chat, limits=budget(), force=True
    )

    assert first is not None and second is not None  # force 不受守护限制


def test_cursor_hook_receives_summary_and_kept_count():
    covered: list = []
    transcript = Transcript(rounds(12))

    run_compaction(
        transcript=transcript,
        state=RunState(workspace_root=str(spill_root)),
        config=CONFIG,
        chat=FakeChat(),
        limits=budget(),
        on_compaction=lambda summary, keep: covered.append((summary, keep)),
    )

    assert len(covered) == 1
    summary, keep = covered[0]
    assert summary["content"].startswith("[历史摘要]")
    assert keep == 20  # 最近 10 轮 = 20 条消息


def test_announce_records_ledger_and_event():
    state = RunState()
    events: list = []
    state.observer = events.append

    announce(
        CompactReport("compact_history", "摘要更早 12 条", 33, 1), state
    )

    assert state.compactions == 1
    assert events[0].type == "context_compacted"
    assert events[0].data["step"] == "compact_history"


def test_summary_call_is_not_capped(spill_root):
    """摘要也是模型调用：写死的上限会被推理吃光，摘要变空 → 这一步静默失效。"""

    class LongChat:
        def __call__(self, config, messages, **kwargs):
            assert "max_tokens" not in kwargs or kwargs["max_tokens"] is None
            return Turn(
                message={"role": "assistant", "content": "这是摘要"},
                text="这是摘要",
                tool_calls=[],
                usage=Usage(1, 1, 2),
                model="m",
                finish_reason="stop",
            )

    transcript = Transcript([user("x" * 2000), assistant("y" * 2000)])

    assert _summarize(transcript.as_messages(), config=CONFIG, chat=LongChat()) == "这是摘要"


def test_summary_message_carries_the_recovery_path():
    text = _summary_message("要点", ".avid/context/transcript-0001.json")

    assert text.startswith("[历史摘要]")
    assert "完整记录：.avid/context/transcript-0001.json" in text
