"""上下文管线编排的测试。

这些用例原来在 test_agent.py 里靠 monkeypatch `agent_module.*` 来验证，
现在编排逻辑住在 context.prepare 里，所以直接在它这一层测——不需要为了
测编排去伪造整个循环。
"""

import pytest

from avid.ai.config import Config
from avid.ai.transcript import Transcript
from avid.policy import compaction as compact
from avid.runtime import context
from avid.runtime.state import RunState

CONFIG = Config(api_key="k", base_url="https://api.test/v1", model="m")


def user(text="hi"):
    return {"role": "user", "content": text}


def summarize(*args, **kwargs):
    raise AssertionError("这个用例不该调用模型")


def test_free_steps_run_before_the_expensive_ones(monkeypatch):
    order = []
    monkeypatch.setattr(
        compact, "tool_result_budget", lambda t, **k: order.append("budget")
    )
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: order.append("snip"))

    context.prepare(Transcript([user()]), RunState(), config=CONFIG, summarize=summarize)

    assert order == ["budget", "snip"]


def test_summary_is_skipped_when_the_free_steps_suffice(monkeypatch):
    """③ 够用就不做 ④——"整理后仍超限才生成摘要"的直接体现。"""
    transcript = Transcript([user("x" * 5000)])
    state = RunState()
    budget = context.ContextBudget(context_chars=100)

    def fake_micro(t, **kwargs):
        t.replace_all([user("short")])
        return compact.CompactReport("micro_compact", "落盘 1 项", 5000, 21)

    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)
    monkeypatch.setattr(compact, "micro_compact", fake_micro)
    monkeypatch.setattr(
        compact, "compact_history", lambda t, **k: pytest.fail("不该生成摘要")
    )

    result = context.prepare(
        transcript, state, config=CONFIG, summarize=summarize, budget=budget
    )

    assert result.changed
    assert state.compacted is False


def test_auto_compaction_happens_at_most_once(monkeypatch):
    transcript = Transcript([user("x" * 5000)])
    state = RunState()
    budget = context.ContextBudget(context_chars=10)

    calls = []

    def fake_history(t, **kwargs):
        calls.append(1)
        return compact.CompactReport("compact_history", "摘要替换", 5000, 10)

    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)
    monkeypatch.setattr(compact, "micro_compact", lambda t, **k: None)
    monkeypatch.setattr(compact, "compact_history", fake_history)

    for _ in range(3):
        context.prepare(
            transcript, state, config=CONFIG, summarize=summarize, budget=budget
        )

    assert calls == [1]
    assert state.compacted is True


def test_steps_below_the_limit_do_nothing(monkeypatch):
    """没超限就不该动 ③④，也不该付一次摘要调用。"""
    transcript = Transcript([user("很短")])
    state = RunState()

    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)
    monkeypatch.setattr(
        compact, "micro_compact", lambda t, **k: pytest.fail("没超限不该瘦身")
    )
    monkeypatch.setattr(
        compact, "compact_history", lambda t, **k: pytest.fail("没超限不该摘要")
    )

    result = context.prepare(
        transcript,
        state,
        config=CONFIG,
        summarize=summarize,
        budget=context.ContextBudget(context_chars=100_000),
    )

    assert not result.changed
    assert state.compactions == 0


def test_each_step_is_announced_and_counted(monkeypatch, caplog):
    monkeypatch.setattr(
        compact,
        "tool_result_budget",
        lambda t, **k: compact.CompactReport("tool_result_budget", "落盘 1 项", 300, 100),
    )
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)

    state = RunState()
    with caplog.at_level("INFO", logger="avid.runtime.context"):
        context.prepare(
            Transcript([user()]), state, config=CONFIG, summarize=summarize
        )

    assert any(
        "compact: tool_result_budget" in record.getMessage()
        for record in caplog.records
    )
    assert state.compactions == 1


def test_multiple_steps_in_one_round_all_count(monkeypatch):
    monkeypatch.setattr(
        compact,
        "tool_result_budget",
        lambda t, **k: compact.CompactReport("tool_result_budget", "落盘 1 项", 300, 100),
    )
    monkeypatch.setattr(
        compact,
        "snip_compact",
        lambda t, **k: compact.CompactReport("snip_compact", "裁掉 10 条", 60, 30),
    )

    state = RunState()
    context.prepare(Transcript([user()]), state, config=CONFIG, summarize=summarize)

    assert state.compactions == 2


def test_reactive_announces_and_counts(monkeypatch):
    monkeypatch.setattr(
        compact,
        "reactive_compact",
        lambda t, **k: compact.CompactReport("reactive_compact", "摘要", 10, 5),
    )

    state = RunState()
    report = context.reactive(
        Transcript([user()]), state, config=CONFIG, summarize=summarize
    )

    assert report is not None
    assert state.compactions == 1


def test_reactive_no_op_is_not_counted(monkeypatch):
    monkeypatch.setattr(compact, "reactive_compact", lambda t, **k: None)

    state = RunState()
    report = context.reactive(
        Transcript([user()]), state, config=CONFIG, summarize=summarize
    )

    assert report is None
    assert state.compactions == 0


def test_default_budget_references_the_compact_constants():
    limits = context.ContextBudget()

    assert limits.tool_result_chars == compact.TOOL_RESULT_CHAR_BUDGET
    assert limits.context_chars == compact.CONTEXT_CHAR_LIMIT
    assert limits.reactive_keep_recent == compact.REACTIVE_KEEP_RECENT


def test_budget_is_injectable_so_orchestration_is_testable(monkeypatch):
    """阈值可注入：测编排时不必改全局常量，也就不必建一个巨大的 transcript。

    免费三步都换掉之后仍然超限（500 > 100），所以第 ④ 步一定会被走到——它也必须
    被换掉，否则这个用例会真的去调模型。`summarize` 哨兵现在会真的炸出来
    （`_summarize` 只吞调用类失败，不再吞 AssertionError），这正是我们要的：
    以前它被宽 except 吞掉，用例"绿着"却发生了模型调用。
    """
    monkeypatch.setattr(compact, "tool_result_budget", lambda t, **k: None)
    monkeypatch.setattr(compact, "snip_compact", lambda t, **k: None)

    seen = []
    monkeypatch.setattr(
        compact,
        "micro_compact",
        lambda t, **k: seen.append(k["limit"]) or None,
    )
    expensive = []
    monkeypatch.setattr(
        compact,
        "compact_history",
        lambda t, **k: expensive.append(k["limit"]) or None,
    )

    context.prepare(
        Transcript([user("x" * 500)]),
        RunState(),
        config=CONFIG,
        summarize=summarize,
        budget=context.ContextBudget(context_chars=100),
    )

    assert seen == [100]
    assert expensive == [100], "整理后仍超限时第 ④ 步应当被走到（且阈值同样可注入）"
