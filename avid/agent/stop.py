"""Termination path: from the model's last tool-call-free turn to the run's end, every named exit.

There is no turn cap — the only budgets are MAX_STOP_BLOCKS for nudges and the two cancellation
checkpoints — and a blank answer always ends in a visible notice, never a silent empty string.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ..providers.client import Turn
from .events import STOP_NUDGE
from .hooks import BLOCK
from .transcript import Transcript

if TYPE_CHECKING:
    from .state import RunState

logger = logging.getLogger("avid.agent.stop")

# Nudge budget, shared by a blocked Stop and a blank answer; one retry by default.
MAX_STOP_BLOCKS = 1

# Standard nudge for a blank answer; a hook-set nudge wins over it.
BLANK_ANSWER_NUDGE = (
    "上一轮没有可见正文（{reason}）。请直接给出可见答复：总结已完成的事与当前结论；"
    "要继续动手就发起工具调用。"
)
# Closing text when there is still no visible body: it must be user-visible, never an empty string.
BLANK_ANSWER_NOTICE = (
    "（本次运行没有产生可见答复：{reason}。请看上一条工具结果，或重试这一轮。）"
)


def is_blank(turn: Turn) -> bool:
    """True when the round has no visible text; truncation and an empty stop look alike here."""
    return not turn.text.strip()


def blank_reason(turn: Turn) -> str:
    """Explain the missing text with concrete numbers rather than an 'unknown' placeholder."""
    thinking = turn.reasoning.strip()
    if thinking:
        base = f"最近一轮只产出了思考（{len(thinking)} 字符思维链）"
    elif turn.finish_reason == "length":
        base = "最近一轮在输出上限处被截断"
    else:
        base = "最近一轮输出为空"
    tokens = turn.usage.reasoning_tokens
    return f"{base}，推理 token {tokens}" if tokens else base


#: Single source for why a run ended; names are append-only since callers compare the literals.
StopReason = Literal[
    "final_text",
    "blank_notice",
    "hook_budget_exit",
    "denial_halted",
    "prompt_blocked",
    "command",
]

STOP_FINAL_TEXT: StopReason = "final_text"
STOP_BLANK_NOTICE: StopReason = "blank_notice"
STOP_HOOK_BUDGET_EXIT: StopReason = "hook_budget_exit"
STOP_DENIAL_HALTED: StopReason = "denial_halted"
# UserPromptSubmit blocked before the first round: the run never began, so the text is empty.
STOP_PROMPT_BLOCKED: StopReason = "prompt_blocked"
# Session command (/compact, unknown-command hint): no model call, the text ends the run at once.
STOP_COMMAND: StopReason = "command"


@dataclass(frozen=True)
class RunOutcome:
    """What one run ended with: the text for the user, and why it stopped."""

    text: str
    reason: StopReason


def decide(
    state: "RunState",
    transcript: Transcript,
    turn: Turn,
    *,
    max_blocks: int,
    emitted: Callable[[dict[str, Any]], None],
) -> RunOutcome | None:
    """Decide a tool-call-free round's end; None means a nudge went out and the loop continues."""
    stop: dict[str, Any] = {
        "final_text": turn.text,
        "messages": transcript.as_messages(),
        "summary": None,
        "nudge": None,
        **state.snapshot(),
    }
    hook_blocked = state.hooks.trigger("Stop", stop) == BLOCK
    blank = is_blank(turn)
    reason = blank_reason(turn) if blank else ""

    # 1. Nudge and continue: a hook block, or a blank answer treated as one (a hook nudge wins).
    if blank and not hook_blocked:
        stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
    blocked = hook_blocked or blank
    if blocked and state.stop_blocks < max_blocks:
        state.stop_blocks += 1
        nudge = stop.get("nudge")
        if nudge:
            message = {"role": "user", "content": str(nudge)}
            transcript.append(message)
            state.emit(STOP_NUDGE, content=str(nudge), message=message)
            emitted(message)
        logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
        return None

    # 2. Blank answer with the budget spent: close with the visible notice, never an empty string.
    if blank:
        notice = BLANK_ANSWER_NOTICE.format(reason=reason)
        message = {"role": "assistant", "content": notice}
        transcript.append(message)
        emitted(message)
        logger.warning(
            "仍然没有可见正文（%s；finish_reason=%s），以 notice 收尾",
            reason,
            turn.finish_reason or "-",
        )
        return RunOutcome(text=notice, reason=STOP_BLANK_NOTICE)
    if hook_blocked:
        logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_blocks)
        return RunOutcome(text=turn.text, reason=STOP_HOOK_BUDGET_EXIT)
    return RunOutcome(text=turn.text, reason=STOP_FINAL_TEXT)
