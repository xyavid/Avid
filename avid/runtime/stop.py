"""终止路径：模型不再请求工具之后、运行结束之前的一段。

输入「无 tool_calls 的那一轮」（调用方已把它 append 进 transcript）与 Stop
hook 裁决，输出最终答复文本或补问信号（StopOutcome.final=None：nudge 已入
transcript，调用方续轮）。补问预算 max_blocks 防止写坏的回调或连续空答复
把循环拖成死循环；预算内 hook 自己的 nudge 优先于空答复的标准补问。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


@dataclass(frozen=True)
class StopOutcome:
    """final=None 表示已补问、调用方应续轮；否则 final 是返回给用户的文本。"""

    final: str | None


def decide(
    state: "RunState",
    transcript: Transcript,
    turn: Turn,
    *,
    max_blocks: int,
    emitted: Callable[[dict[str, Any]], None],
) -> StopOutcome:
    """裁决一轮无 tool_calls 的结束：放行、补问续轮，或以可见 notice 收尾。"""
    stop: dict[str, Any] = {
        "final_text": turn.text,
        "messages": transcript.as_messages(),
        "summary": None,
        "nudge": None,
        **state.snapshot(),
    }
    blocked = state.hooks.trigger("Stop", stop) == BLOCK
    blank = is_blank(turn)
    reason = blank_reason(turn) if blank else ""
    if blank and not blocked:
        # 没有可见正文的一轮不算答复：按一次 Stop 拦截处理并补问
        blocked = True
        stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
    if blocked and state.stop_blocks < max_blocks:
        state.stop_blocks += 1
        nudge = stop.get("nudge")
        if nudge:
            message = {"role": "user", "content": str(nudge)}
            transcript.append(message)
            state.emit(STOP_NUDGE, content=str(nudge), message=message)
            emitted(message)
        logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
        return StopOutcome(final=None)
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
        return StopOutcome(final=notice)
    if blocked:
        logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_blocks)
    return StopOutcome(final=turn.text)
