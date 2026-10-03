"""上下文管线编排的测试：compose() 先跑压缩通路，再装配。

编排逻辑委托给 compaction.py，这一层只验证"何时触发、触发后装配到什么"，
不需要为了测编排去伪造整个循环。
"""


from avid.agent import compaction as compact
from avid.agent.compaction import ContextBudget
from avid.agent.context import ContextManager
from avid.agent.state import RunState
from avid.agent.transcript import Transcript
from avid.providers.client import LLMError, Turn, Usage
from avid.providers.config import Config

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def user(text="hi"):
    return {"role": "user", "content": text}


def summarize_sentinel(*args, **kwargs):
    raise AssertionError("这个用例不该调用模型")


class Summarizer:
    """固定返回摘要文本的假摘要模型，记录每次调用。"""

    def __init__(self, text="要点摘要"):
        self.calls = 0
        self.text = text

    def __call__(self, config, messages, **kwargs):
        self.calls += 1
        return Turn(
            message={"role": "assistant", "content": self.text},
            text=self.text,
            tool_calls=[],
            usage=Usage(1, 1, 2),
            model="m",
            finish_reason="stop",
        )


def rounds(count, content="x" * 200):
    messages = [user("任务")]
    for index in range(count):
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{index}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": content})
    return messages


def budget(**overrides):
    defaults = {"keep_recent_turns": 10, "context_chars": 10, "from_window": False}
    defaults.update(overrides)
    return ContextBudget(**defaults)


def prepare(transcript, state, *, budget=None, summarize=summarize_sentinel,
            on_compaction=None):
    """compose 的压缩半程：本文件只关心编排，不关心渲染。"""
    manager = ContextManager(
        transcript=transcript,
        state=state,
        config=CONFIG,
        summarize=summarize,
        budget=budget,
        on_compaction=on_compaction,
    )
    return manager.compose()


def reactive(transcript, state, *, budget=None, summarize=summarize_sentinel,
             on_compaction=None):
    manager = ContextManager(
        transcript=transcript, state=state, config=CONFIG, summarize=summarize,
        budget=budget, on_compaction=on_compaction,
    )
    return manager.reactive()


def test_below_trigger_nothing_happens():
    transcript = Transcript([user("很短")])
    state = RunState()

    result = prepare(transcript, state, budget=budget(context_chars=100_000))

    assert not result.changed
    assert state.compactions == 0
    assert state.compacted is False


def test_above_trigger_summarizes_once_and_persists_cursor():
    transcript = Transcript(rounds(12))
    state = RunState()
    summarizer = Summarizer()
    covered = []

    result = prepare(
        transcript,
        state,
        budget=budget(),
        summarize=summarizer,
        on_compaction=lambda summary, keep: covered.append((summary, keep)),
    )

    assert result.changed
    assert summarizer.calls == 1
    assert state.compacted is True
    assert len(covered) == 1
    summary, keep = covered[0]
    assert summary["content"].startswith("[历史摘要]")
    assert keep == 20  # 最近 10 轮 = 20 条消息
    assert transcript.as_messages()[0]["content"] == summary["content"]


def test_auto_compaction_happens_at_most_once():
    transcript = Transcript(rounds(12))
    state = RunState()
    summarizer = Summarizer()

    for _ in range(3):
        prepare(transcript, state, budget=budget(), summarize=summarizer)

    assert summarizer.calls == 1
    assert state.compacted is True


def test_force_bypasses_threshold_and_the_once_guard():
    transcript = Transcript(rounds(12))
    state = RunState()
    summarizer = Summarizer()

    prepare(transcript, state, budget=budget(), summarize=summarizer)
    report = reactive(transcript, state, budget=budget(), summarize=summarizer)

    assert report is not None
    assert summarizer.calls == 2  # force 不受每运行一次的守护限制


def test_summarize_failure_keeps_history():
    class Failing:
        def __call__(self, config, messages, **kwargs):
            raise LLMError("端点挂了")

    transcript = Transcript(rounds(12))
    state = RunState()
    before = transcript.as_messages()

    result = prepare(transcript, state, budget=budget(), summarize=Failing())

    assert not result.changed
    assert state.compacted is False
    assert transcript.as_messages() == before


def test_each_compaction_is_announced_and_counted(caplog):
    transcript = Transcript(rounds(12))
    state = RunState()
    summarizer = Summarizer()

    with caplog.at_level("INFO", logger="avid.agent.compaction"):
        prepare(transcript, state, budget=budget(), summarize=summarizer)

    assert any(
        "compact: compact_history" in record.getMessage() for record in caplog.records
    )
    assert state.compactions == 1


def test_compaction_arms_the_next_real_reading():
    """压缩之后要等下一轮真实读数：编排只置"等读数"的标志，回填由 record_usage 做。"""
    transcript = Transcript(rounds(12))
    state = RunState()
    state.record_usage(Usage(150_000, 1, 150_001))

    prepare(transcript, state, budget=budget(), summarize=Summarizer())

    # 压完还没调用模型：不给数（界面显示「—」），也不猜。
    assert state.usage_report()["compaction"]["last_compaction_tokens"] is None

    state.record_usage(Usage(40_000, 1, 40_001))
    assert state.usage_report()["compaction"] == {
        "count": 1,
        "last_compaction_tokens": 40_000,
        "last_step": "compact_history",
    }


def test_default_budget_references_the_compact_constants():
    limits = ContextBudget()

    assert limits.keep_recent_turns == compact.KEEP_RECENT_TURNS
    assert limits.reserve_tokens == compact.RESERVE_TOKENS
    assert limits.context_chars == compact.CONTEXT_CHAR_LIMIT
    assert limits.bootstrap_chars == compact.prompt.AGENTS_MD_MAX_CHARS
    assert limits.skill_always_chars == compact.prompt.SKILL_ALWAYS_TOTAL_MAX_CHARS


def test_budget_is_injectable_so_orchestration_is_testable():
    """阈值可注入：测编排时不必改全局常量，也就不必建一个巨大的 transcript。"""
    transcript = Transcript(rounds(12))
    state = RunState()
    summarizer = Summarizer()

    # 触发线抬高到永远够不着：摘要不该被调用
    prepare(
        transcript, state,
        budget=budget(context_chars=100_000_000), summarize=summarizer,
    )
    assert summarizer.calls == 0
