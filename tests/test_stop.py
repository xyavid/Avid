"""Contract of the stop path: a round without tool_calls, already appended to the transcript, plus
the Stop hook verdict goes in; the final text or a nudge comes out.

A nudge returns final=None with the nudge already in the transcript and the caller continues; the
max_blocks budget keeps a broken callback or repeated blank answers from looping forever.
"""

from __future__ import annotations

from support import make_turn

from avid.agent.events import STOP_NUDGE
from avid.agent.hooks import BLOCK, HookRegistry
from avid.agent.state import RunState
from avid.agent.stop import (
    MAX_STOP_BLOCKS,
    STOP_BLANK_NOTICE,
    STOP_FINAL_TEXT,
    STOP_HOOK_BUDGET_EXIT,
    RunOutcome,
    blank_reason,
    decide,
    is_blank,
)
from avid.agent.transcript import Transcript

USER = {"role": "user", "content": "问"}


def make_state(**kwargs):
    return RunState.for_run(**kwargs)


def append_and_decide(state, transcript, turn, *, max_blocks=MAX_STOP_BLOCKS, emitted=None):
    """Real ordering: append the round to the transcript, then run the stop verdict."""
    transcript.append(turn.message)
    sink = emitted if emitted is not None else []
    return decide(
        state,
        transcript,
        turn,
        max_blocks=max_blocks,
        emitted=sink.append,
    )


def test_visible_answer_returns_final_without_nudge():
    state = make_state(hooks=HookRegistry())
    transcript = Transcript([dict(USER)])
    emitted: list[dict] = []
    outcome = append_and_decide(state, transcript, make_turn("答复"), emitted=emitted)
    assert outcome == RunOutcome(text="答复", reason=STOP_FINAL_TEXT)
    assert state.stop_blocks == 0
    assert emitted == []


def test_blank_answer_triggers_one_nudge_then_closes_with_notice():
    events: list = []
    state = make_state(hooks=HookRegistry(), observer=events.append)
    transcript = Transcript([dict(USER)])
    emitted: list[dict] = []

    first = append_and_decide(state, transcript, make_turn(""), emitted=emitted)
    assert first is None
    assert state.stop_blocks == 1
    assert any(e.type == STOP_NUDGE for e in events)
    nudges = [
        m["content"]
        for m in transcript.as_messages()
        if m["role"] == "user" and m["content"].startswith("上一轮没有可见正文")
    ]
    assert len(nudges) == 1
    assert len(emitted) == 1  # the nudge goes through the on_message channel

    # Budget spent: another blank answer closes with a visible notice, never silently.
    second = append_and_decide(state, transcript, make_turn(""), emitted=emitted)
    assert second.reason == STOP_BLANK_NOTICE
    assert second.text.startswith("（本次运行没有产生可见答复")
    assert transcript.as_messages()[-1]["role"] == "assistant"
    assert len(emitted) == 2


def test_stop_hook_can_hold_the_exit_open_with_its_own_nudge():
    registry = HookRegistry()

    @registry.register("Stop")
    def hold(stop):
        stop["nudge"] = "请总结一下"
        return BLOCK

    state = make_state(hooks=registry)
    transcript = Transcript([dict(USER)])
    outcome = append_and_decide(state, transcript, make_turn("有正文但被拦"))
    assert outcome is None
    assert transcript.as_messages()[-1] == {"role": "user", "content": "请总结一下"}


def test_hook_nudge_wins_when_it_blocks_a_blank_round():
    registry = HookRegistry()

    @registry.register("Stop")
    def hold(stop):
        stop["nudge"] = "hook 补问"
        return BLOCK

    state = make_state(hooks=registry)
    transcript = Transcript([dict(USER)])
    outcome = append_and_decide(state, transcript, make_turn(""))
    assert outcome is None
    assert transcript.as_messages()[-1] == {"role": "user", "content": "hook 补问"}


def test_block_budget_exhausted_returns_text_and_stops_counting():
    registry = HookRegistry()

    @registry.register("Stop")
    def always_block(stop):
        return BLOCK

    state = make_state(hooks=registry)
    transcript = Transcript([dict(USER)])
    first = append_and_decide(state, transcript, make_turn("一"))
    assert first is None
    second = append_and_decide(state, transcript, make_turn("二"))
    assert second == RunOutcome(text="二", reason=STOP_HOOK_BUDGET_EXIT)
    assert state.stop_blocks == 1  # no further counting once the budget is spent


def test_blank_reason_names_thinking_truncation_or_empty():
    reason_thinking = blank_reason(make_turn("", reasoning="思" * 20))
    assert "思考" in reason_thinking and "20 字符" in reason_thinking
    reason_truncated = blank_reason(make_turn("", finish_reason="length"))
    assert "截断" in reason_truncated
    assert "输出为空" in blank_reason(make_turn(""))


def test_is_blank_distinguishes_whitespace_from_text():
    assert is_blank(make_turn("  \n")) is True
    assert is_blank(make_turn("答案")) is False
