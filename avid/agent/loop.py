"""Agent execution loop: it decides only the order of model calls, tool batches and termination."""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..providers.byok import resolve_chat
from ..providers.client import (
    DEFAULT_MAX_TOKENS,
    PromptTooLongError,
    Turn,
    chat_completion,
    fetch_context_length,
)
from ..providers.config import Config
from ..providers.transcript import Transcript
from . import events
from .context import ComposedRequest, ContextBudget, ContextManager
from .events import RunObserver
from .execution import execute_batch
from .hooks import BLOCK, HookRegistry
from .state import MAX_CONSECUTIVE_DENIALS, RunState
from .stop import (
    BLANK_ANSWER_NOTICE,
    BLANK_ANSWER_NUDGE,
    MAX_STOP_BLOCKS,
)
from .stop import (
    blank_reason as _blank_reason,
)
from .stop import (
    is_blank as _blank_answer,
)
from .tools import TOOL_IMPLS, TOOLS, ToolImpl

# Only annotations use it: annotations are lazy, so the policy layer stays out of run time.
if TYPE_CHECKING:
    from ..security.permission import ApprovalLedger, AskUser, RunSecurity

logger = logging.getLogger("avid.agent.loop")

class RunCancelled(RuntimeError):
    """Cancellation, raised only at step boundaries so no compensating write is needed."""


def _submit_input(
    transcript: Transcript, state: RunState, tool_names: list[str]
) -> tuple[int, list[str]] | None:
    """Run the submit hook; returns the trigger message index and injected context, or None."""
    index = transcript.last_user_index()
    if index is None:
        return None

    submit: dict[str, Any] = {
        "prompt": transcript.text_at(index),
        "messages": transcript.as_messages(),
        "injected": [],
        # The injected environment information must match the workspace actually resolved.
        "workspace_root": state.workspace_root,
        "permission_mode": state.permission_mode,
        "tool_names": list(tool_names),
    }
    if state.hooks.trigger("UserPromptSubmit", submit) == BLOCK:
        logger.warning("UserPromptSubmit 被拦截，未调用模型")
        return None

    return index, [str(item) for item in (submit.get("injected") or [])]


def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    # Summary calls (compaction, fallback) get their own entry point so that a streaming
    # main round never mixes summary text into the delta stream.
    summarize: Callable[..., Turn] | None = None,
    # Injecting the thresholds keeps a measurement run free of unrelated code changes.
    budget: ContextBudget | None = None,
    auto_approve: bool = False,
    # A shared ledger lets one approval by the caller cover a whole run, subagents included.
    permission_mode: str | None = None,
    ledger: "ApprovalLedger | None" = None,
    security: "RunSecurity | None" = None,
    workspace_root: str | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    # Denials counted without a single pass in between; reaching it ends the run.
    max_consecutive_denials: int = MAX_CONSECUTIVE_DENIALS,
    # None uses config.max_parallel_tool_calls; 1 is fully serial; only concurrency-safe
    # tools share a segment, everything else is a barrier.
    max_parallel_tools: int | None = None,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    ask: AskUser | None = None,
    on_event: RunObserver | None = None,
    state: RunState | None = None,
    hooks: HookRegistry | None = None,
) -> str:
    """Cycle model calls and tool batches until the model stops asking; returns the final text."""
    config = config or resolve_chat()
    if config.context_window is None:
        # Ask the provider once per process when neither the environment nor the built-in
        # table knows the window; every run path passes through here, so no caller repeats it.
        probed = fetch_context_length(config)
        if probed:
            config = replace(config, context_window=probed)
    summarize = summarize or chat
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry
    # Single place where the concurrency limit is resolved: argument first, config second.
    parallel_limit = (
        config.max_parallel_tool_calls
        if max_parallel_tools is None
        else max_parallel_tools
    )

    # Sole message channel: every message this run creates or rewrites is reported once, in order.
    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    # Registry and system prompt belong to state, and a supplied state is the sole authority,
    # so the convenience arguments above are ignored in that case.
    state = state or RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=on_event,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        hooks=hooks,
        # The window is part of the model config, so the utilization denominator follows it.
        context_window=config.context_window,
    )
    # Callers that build state first see a None window, and the probe happens only here, so
    # without this backfill the web view would never show utilization.
    if state.context_window is None:
        state.context_window = config.context_window

    # Context assembly, compaction and the tail blocks belong to the manager; the loop hands
    # over only this run's facts: instruction override, real tool list, budget, summarizer.
    ctx = ContextManager(
        transcript=Transcript(messages),
        state=state,
        config=config,
        instructions=system,
        tool_names=[str(item["function"]["name"]) for item in tools],
        budget=budget,
        summarize=summarize,
    )
    transcript = ctx.transcript

    trigger = _submit_input(transcript, state, [str(item["function"]["name"]) for item in tools])
    if trigger is None:
        return ""
    index, injected = trigger
    emit(transcript.as_messages()[index])

    def note_prompt_parts(request: ComposedRequest) -> None:
        """Record the character counts of the three blocks about to be sent."""
        # System prompt and tool definitions never reach the client, so the split can only be
        # measured here; usage_report() turns the character shares into token estimates.
        # Tail blocks count as messages because they are sent, even though they are not stored.
        state.record_prompt_parts(
            system=request.system_chars,
            tools=len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
            messages=request.messages_chars,
        )

    # No round cap by design, since the exits are the model's answer and the two cancel
    # checkpoints; this file must stay one scheduler, not a handwritten state machine.
    for round_index in itertools.count(1):
        state.round = round_index
        state.check_cancelled()  # Cancel checkpoint 1: before the round starts.

        state.emit(events.RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

        # Compaction steps run inside compose: the cheap ones every round, the costly ones
        # only past budget, and the last of them at most once per run.
        request = ctx.compose(injected=injected)

        # Model call; an over-context error triggers one fallback compaction and one retry.
        note_prompt_parts(request)
        try:
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            ctx.reactive()
            # Reactive compaction changed the candidates, so tail and parts are recomputed.
            request = ctx.render()
            note_prompt_parts(request)
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )

        state.record_usage(turn.usage)
        transcript.append(turn.message)
        emit(turn.message)
        # The snapshot follows this round's real reading; as a transient event it stays out
        # of the replay budget, so a refresh falls back to the value persisted in the session.
        state.emit(
            events.RUN_STATUS,
            round=round_index,
            tokens=state.tokens,
            activity="model",
            finish_reason=turn.finish_reason,
            usage=state.usage_report(),
        )
        logger.info(
            "round=%d finish=%s tool_calls=%d tokens=%d",
            round_index,
            turn.finish_reason or "-",
            len(turn.tool_calls),
            turn.usage.total_tokens,
        )

        if not turn.tool_calls:
            # Stop path: a callback may ask to hold the exit open.
            stop: dict[str, Any] = {
                "final_text": turn.text,
                "messages": transcript.as_messages(),
                "summary": None,
                "nudge": None,
                **state.snapshot(),
            }
            blocked = state.hooks.trigger("Stop", stop) == BLOCK
            blank = _blank_answer(turn)
            reason = _blank_reason(turn) if blank else ""
            if blank and not blocked:
                # A round without visible text is not an answer: treat it as a blocked Stop
                # and ask again, letting a callback's own nudge win when it set one.
                blocked = True
                stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
            if blocked and state.stop_blocks < max_stop_blocks:
                state.stop_blocks += 1
                nudge = stop.get("nudge")
                if nudge:
                    message = {"role": "user", "content": str(nudge)}
                    transcript.append(message)
                    state.emit(events.STOP_NUDGE, content=str(nudge), message=message)
                    emit(message)
                logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
                continue
            if blank:
                # Even the follow-up produced nothing: close with visible text rather than
                # returning an empty string, which is the "success with no answer" path.
                notice = BLANK_ANSWER_NOTICE.format(reason=reason)
                message = {"role": "assistant", "content": notice}
                transcript.append(message)
                emit(message)
                logger.warning(
                    "仍然没有可见正文（%s；finish_reason=%s，round=%d），以 notice 收尾",
                    reason,
                    turn.finish_reason or "-",
                    round_index,
                )
                return notice
            if blocked:
                logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_stop_blocks)
            return turn.text

        state.check_cancelled()  # Cancel checkpoint 2: before each tool batch.
        outcomes = execute_batch(
            turn.tool_calls,
            state=state,
            registry=registry,
            round_index=round_index,
            # Argument validation reuses the very schema that was sent to the model.
            schemas={
                str(item["function"]["name"]): item["function"]["parameters"]
                for item in tools
            },
            # In-batch concurrency: safe tools share a segment, exclusive calls are barriers.
            max_parallel=parallel_limit,
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            transcript.append(message)
            emit(message)

        if state.denial_streak >= max_consecutive_denials:
            # Denied again and again without a single pass means the same wall is being hit;
            # the halt text travels as a normal message, so no new event type is needed.
            halt = (
                f"（运行已停止：连续 {state.denial_streak} 次工具调用被权限策略拒绝，"
                "期间没有一次通过。请向用户说明需要哪个目标或哪条命令的授权，"
                "再开新一轮。）"
            )
            logger.warning(
                "连续 %d 次工具调用被拒，运行提前结束", state.denial_streak
            )
            message = {"role": "assistant", "content": halt}
            transcript.append(message)
            emit(message)
            return halt
    # Static checkers do not accept an endless loop, so the exit has to be spelled out even
    # though run time never reaches it: the loop leaves through return, cancel or an error.
    raise AssertionError("轮次循环没有正常出口")
