"""终止路径：模型不再请求工具之后、运行结束之前的一段。

判定是显式阶梯，每个出口都叫得出名字（StopReason，出口名单点）：

  1. hook 拦截且预算内 → 补问续轮（decide 返回 None）
  2. 空答复视同拦截：预算内补问续轮；预算用尽 → BLANK_NOTICE（绝不静默）
  3. hook 拦截且预算用尽 → HOOK_BUDGET_EXIT（按原文退出）
  4. 正常可见正文 → FINAL_TEXT

参考实现的三个分支在这里的明确取舍：
  handoff（模型换 agent）——内核没有模型驱动的 agent 切换（subagent 是工具）；
    出现真实需求时在 run 循环加分支，不进本模块。
  terminal tool（工具结果即最终答复）——现在没有这种工具；出现
    structured-output / ask_user-as-final 类工具时，在 run 循环加
    should_stop_after_tools 分支并给它一个 StopReason。
  MAX_TURNS（轮数硬上限）——按 limit-audit 裁定不存在：轮数不是收敛判据，
    预算只有补问（MAX_STOP_BLOCKS）与两个取消检查点。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ..ai.client import Turn
from ..ai.transcript import Transcript
from .events import STOP_NUDGE
from .hooks import BLOCK

if TYPE_CHECKING:
    from .state import RunState

logger = logging.getLogger("avid.runtime.stop")

# 补问预算：被拦截的 Stop 与空答复共用，默认只给一轮机会。
MAX_STOP_BLOCKS = 1

# 空答复的标准补问；hook 自己设置的 nudge 优先于它。
BLANK_ANSWER_NUDGE = (
    "上一轮没有可见正文（{reason}）。请直接给出可见答复：总结已完成的事与当前结论；"
    "要继续动手就发起工具调用。"
)
# 补问之后仍无正文时的收尾文本；它必须对用户可见，绝不静默返回空串。
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


#: 出口名单点：一次运行为什么结束。名字只增不改（前端/调用方可能对比字面量）。
StopReason = Literal[
    "final_text",
    "blank_notice",
    "hook_budget_exit",
    "denial_halted",
    "prompt_blocked",
]

STOP_FINAL_TEXT: StopReason = "final_text"
STOP_BLANK_NOTICE: StopReason = "blank_notice"
STOP_HOOK_BUDGET_EXIT: StopReason = "hook_budget_exit"
STOP_DENIAL_HALTED: StopReason = "denial_halted"
# UserPromptSubmit hook 在第一轮之前拦截：运行根本没开始，文本为空。
STOP_PROMPT_BLOCKED: StopReason = "prompt_blocked"


@dataclass(frozen=True)
class RunOutcome:
    """一次运行的结束：返回给用户的文本，以及它为什么结束。"""

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
    """裁决一轮无 tool_calls 的结束：返回结束结果，None = 已补问、调用方续轮。"""
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

    # 1. 补问续轮：hook 拦截，或空答复视同拦截（hook 的 nudge 优先于标准补问）
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

    # 2. 空答复且预算用尽：可见 notice 收尾（绝不静默返回空串）
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
