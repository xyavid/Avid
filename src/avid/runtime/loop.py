"""Agent 循环：只表达调度顺序。

它不认识阈值、文案与协议细节——

* 消息结构与不变量：``ai/transcript.py``
* 运行状态与一次性标志：``runtime/state.py``
* 压缩编排：``runtime/context.py``
* 工具调用协议：``runtime/execution.py``
* 扩展点：``runtime/hooks.py``

它对 ``policy/`` **零依赖**——阈值、文案、规则与注册表都经 ``state`` 与事件间接取得。

它只回答：什么时候调模型、什么时候跑工具、什么时候停。

协议映射（Anthropic 语义 → OpenAI 兼容）：
  content 里的 tool_use 块        → message.tool_calls[]
  tool_result 的一条 user 消息    → 每次调用一条 {"role": "tool", ...}
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from . import context
from ..ai.config import Config, load_config
from .execution import execute_batch
from .hooks import BLOCK, trigger_hooks
from ..ai.client import DEFAULT_MAX_TOKENS, PromptTooLongError, Turn, chat_completion
from ..tools import TOOL_IMPLS, TOOLS, ToolImpl
from .state import TODO_REMINDER_AFTER_ROUNDS, RunState
from ..ai.transcript import Transcript

logger = logging.getLogger("avid.runtime.loop")

MAX_ROUNDS = 8

# Stop 被拦截后最多再补几轮。防止写坏的回调把循环拖成死循环。
MAX_STOP_BLOCKS = 1


class RoundLimitExceeded(RuntimeError):
    """连续多轮都在调用工具，未收敛。后续会把它改成可分类的终止原因。"""


def _calls_todo_write(tool_calls: list[dict[str, Any]]) -> bool:
    """本轮是否更新过 TODO —— 用来决定提醒计数是归零还是累加。"""
    return any(
        (call.get("function") or {}).get("name") == "todo_write" for call in tool_calls
    )


def _submit_input(transcript: Transcript, state: RunState) -> int | None:
    """UserPromptSubmit：可注入上下文，也可拦截整个输入。

    返回值是**本次运行的触发消息**下标；None 表示这次不跑（没有用户消息，或被拦截）。
    下标要返回出去，是因为注入会改写那条消息——会话落库要的是改写后的版本。
    """
    index = transcript.last_user_index()
    if index is None:
        return None

    submit: dict[str, Any] = {
        "prompt": transcript.text_at(index),
        "messages": transcript.as_messages(),
        "injected": [],
    }
    if trigger_hooks("UserPromptSubmit", submit) == BLOCK:
        logger.warning("UserPromptSubmit 被拦截，未调用模型")
        return None

    if submit["injected"]:
        transcript.set_content(
            index,
            "\n".join(str(item) for item in submit["injected"])
            + "\n\n"
            + submit["prompt"],
        )
    return index


def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    auto_approve: bool = False,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_rounds: int = MAX_ROUNDS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    todo_reminder_after: int = TODO_REMINDER_AFTER_ROUNDS,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """跑到模型不再要工具为止，返回最后一轮的 assistant 文本。

    ``messages`` 原地更新：每轮的 assistant 消息与工具结果都会写回同一个 list。
    ``system`` 是「固定指令部分」，技能目录由 SkillLoader 统一追加。

    ``on_message`` 是循环**唯一的对外观察点**：本次运行产生或改写的每条消息按
    发生顺序回调一次——先是触发用户消息（UserPromptSubmit 注入**之后**的版本），
    然后是每轮追加的 assistant、工具结果、注入的 TODO 提醒与 Stop nudge。
    循环不 import 会话层，落库与否由回调决定（不变量 I7）。
    """
    config = config or load_config()
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry

    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    transcript = Transcript(messages)
    # 注册表与 system prompt 都由 state 负责——循环不知道默认指令文案，也不持有注册表。
    state = RunState.for_run(auto_approve=auto_approve)
    system_prompt = state.system_prompt(system)

    trigger = _submit_input(transcript, state)
    if trigger is None:
        return ""
    emit(transcript.as_messages()[trigger])

    for round_index in range(1, max_rounds + 1):
        state.round = round_index

        # TODO 提醒依赖"第几轮"，这确实是循环自身的事实；
        # 但"该不该提醒、提醒什么"由 state 决定，循环只负责追加。
        reminder = state.todo_reminder(todo_reminder_after)
        if reminder is not None:
            message = {"role": "user", "content": reminder}
            transcript.append(message)
            emit(message)
            logger.info("注入 TODO 提醒（连续 %d 轮未更新）", state.rounds_since_todo)

        # 上下文管线：①② 每轮跑，③④ 超限时才跑，④ 整个运行最多一次
        context.prepare(transcript, state, config=config, summarize=chat)

        # 模型调用；报上下文超限时兜底压缩并重试一次（整个运行最多一次）
        try:
            turn = chat(
                config,
                transcript.as_messages(),
                system=system_prompt,
                tools=tools,
                max_tokens=max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            context.reactive(transcript, state, config=config, summarize=chat)
            turn = chat(
                config,
                transcript.as_messages(),
                system=system_prompt,
                tools=tools,
                max_tokens=max_tokens,
            )

        transcript.append(turn.message)
        emit(turn.message)
        logger.info(
            "round=%d finish=%s tool_calls=%d tokens=%d",
            round_index,
            turn.finish_reason or "-",
            len(turn.tool_calls),
            turn.usage.total_tokens,
        )

        state.rounds_since_todo = (
            0 if _calls_todo_write(turn.tool_calls) else state.rounds_since_todo + 1
        )

        if not turn.tool_calls:
            # Stop：回调可以要求"先别退出"
            stop: dict[str, Any] = {
                "final_text": turn.text,
                "messages": transcript.as_messages(),
                "summary": None,
                "nudge": None,
                **state.snapshot(),
            }
            blocked = trigger_hooks("Stop", stop) == BLOCK
            if blocked and state.stop_blocks < max_stop_blocks:
                state.stop_blocks += 1
                nudge = stop.get("nudge")
                if nudge:
                    message = {"role": "user", "content": str(nudge)}
                    transcript.append(message)
                    emit(message)
                logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
                continue
            if blocked:
                logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_stop_blocks)
            return turn.text

        outcomes = execute_batch(
            turn.tool_calls, state=state, registry=registry, round_index=round_index
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            transcript.append(message)
            emit(message)

    raise RoundLimitExceeded(f"达到轮数上限 {max_rounds}，模型仍在请求工具，未收敛")
