"""压缩预算（ContextBudget）与触发线（reserve 语义）的测试。"""

from avid.agent.compaction import (
    CONTEXT_CHAR_LIMIT,
    KEEP_RECENT_TURNS,
    RESERVE_TOKENS,
    ContextBudget,
    effective_trigger,
)
from avid.agent.state import RunState
from avid.providers.client import Usage

WINDOW = 200_000


def state_with_readings():
    """一份带真实读数的运行状态：窗口 200k，上一轮 100k prompt tokens。"""
    state = RunState(context_window=WINDOW)
    state.last_usage = Usage(100_000, 1, 100_001)
    state.prompt_parts = (1000, 500, 50_000)
    return state


def test_trigger_is_window_minus_reserve_times_measured_rate():
    _limits, trigger = effective_trigger(ContextBudget(), state_with_readings())

    per_token = 51_500 / 100_000
    expected = int((WINDOW - 16_384) * per_token) - 1500
    assert trigger == expected


def test_trigger_scales_down_when_reserve_grows():
    _limits, small = effective_trigger(
        ContextBudget(reserve_tokens=16_384), state_with_readings()
    )
    _limits, big = effective_trigger(
        ContextBudget(reserve_tokens=65_536), state_with_readings()
    )
    assert big < small


def test_trigger_falls_back_without_a_window():
    _limits, trigger = effective_trigger(ContextBudget(), RunState())
    assert trigger == CONTEXT_CHAR_LIMIT


def test_fallback_honors_the_injected_constant():
    _limits, trigger = effective_trigger(
        ContextBudget(context_chars=123_456, from_window=False), state_with_readings()
    )
    assert trigger == 123_456


def test_default_budget_references_the_constants():
    limits = ContextBudget()

    assert limits.keep_recent_turns == KEEP_RECENT_TURNS
    assert limits.reserve_tokens == RESERVE_TOKENS
    assert limits.context_chars == CONTEXT_CHAR_LIMIT
