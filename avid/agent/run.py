"""Run：一次运行的生命周期（prepare → 轮次循环 → 终止），单出口。

与 agent_loop 的分工：这里只换表达方式（显式阶段方法 + 单一出口），
行为逐字节一致由 tests/test_run.py 的序列一致性用例钉住；阶段 37 起
行为演进先改这里，旧 loop.py 在阶段 40 删除（届时 BLANK_* 与
MAX_STOP_BLOCKS 常量随终止路径迁往 stop 模块）。
"""

from __future__ import annotations

import itertools
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..providers.client import PromptTooLongError, Turn
from .context import ComposedRequest, ContextManager
from .events import RUN_STATUS, RunObserver
from .execution import execute_batch
from .hooks import BLOCK
from .spec import RunSpec
from .state import RunState
from .stop import STOP_DENIAL_HALTED, STOP_PROMPT_BLOCKED, RunOutcome, decide
from .transcript import Transcript

if TYPE_CHECKING:
    from ..security.permission import AskUser

logger = logging.getLogger("avid.agent.run")


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


class Run:
    def __init__(
        self,
        messages: list[dict[str, Any]],
        spec: RunSpec,
        *,
        state: RunState | None = None,
        on_message: Callable[[dict[str, Any]], Any] | None = None,
        ask: "AskUser | None" = None,
        on_event: RunObserver | None = None,
        # ④/⑤ 替换历史后的落盘钩子（summary 消息, keep 条数）；CLI 接会话游标，
        # 不接则压缩只在内存生效、下个运行重新压缩（诊断 C2 的旧行为）。
        on_compaction: Callable[[dict[str, Any], int], None] | None = None,
    ) -> None:
        self.messages = messages
        self.spec = spec
        self.state = state
        self.on_message = on_message
        self.ask = ask
        self.on_event = on_event
        self.on_compaction = on_compaction

    def run(self) -> RunOutcome:
        spec = self.spec
        state = self.state
        if state is None:
            state = RunState.for_run(
                auto_approve=spec.auto_approve,
                ask=self.ask,
                observer=self.on_event,
                permission_mode=spec.permission_mode,
                ledger=spec.ledger,
                security=spec.security,
                workspace_root=spec.workspace_root,
                hooks=spec.hooks,
                context_window=spec.config.context_window,
            )
        # 调用方自建 state 时窗口还没探测（探测只发生在 resolve），这里回填。
        if state.context_window is None:
            state.context_window = spec.config.context_window

        ctx = ContextManager(
            transcript=Transcript(self.messages),
            state=state,
            config=spec.config,
            instructions=spec.instructions,
            tool_names=spec.tool_names,
            budget=spec.budget,
            summarize=spec.summarize or spec.chat,
            on_compaction=self.on_compaction,
        )
        transcript = ctx.transcript

        trigger = _submit_input(transcript, state, spec.tool_names)
        if trigger is None:
            # UserPromptSubmit 拦截：运行没开始就结束，但没有模型轮次可补问
            return RunOutcome(text="", reason=STOP_PROMPT_BLOCKED)
        index, injected = trigger
        self._emit(transcript.as_messages()[index])

        for round_index in itertools.count(1):
            state.round = round_index
            state.check_cancelled()  # 取消检查点 1：轮次开始前
            state.emit(RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

            request = ctx.compose(injected=injected)
            self._note_prompt_parts(state, request)
            turn = self._call_model(state, ctx, request)

            state.record_usage(turn.usage)
            transcript.append(turn.message)
            self._emit(turn.message)
            state.emit(
                RUN_STATUS,
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
                final = self._finish(state, transcript, turn)
                if final is not None:
                    return final
                continue

            state.check_cancelled()  # 取消检查点 2：工具批前
            outcomes = execute_batch(
                turn.tool_calls,
                state=state,
                registry=spec.registry,
                round_index=round_index,
                schemas=spec.schemas,
                max_parallel=spec.parallel_limit,
            )
            for outcome in outcomes:
                message = {
                    "role": "tool",
                    "tool_call_id": outcome.tool_call_id,
                    "content": outcome.content,
                }
                transcript.append(message)
                self._emit(message)

            if state.denial_streak >= spec.max_consecutive_denials:
                halt = (
                    f"（运行已停止：连续 {state.denial_streak} 次工具调用被权限策略拒绝，"
                    "期间没有一次通过。请向用户说明需要哪个目标或哪条命令的授权，"
                    "再开新一轮。）"
                )
                logger.warning("连续 %d 次工具调用被拒，运行提前结束", state.denial_streak)
                message = {"role": "assistant", "content": halt}
                transcript.append(message)
                self._emit(message)
                return RunOutcome(text=halt, reason=STOP_DENIAL_HALTED)
        raise AssertionError("轮次循环没有正常出口")  # pragma: no cover

    def _call_model(
        self, state: RunState, ctx: ContextManager, request: ComposedRequest
    ) -> Turn:
        spec = self.spec
        try:
            return spec.chat(
                spec.config,
                request.messages,
                system=request.system,
                tools=spec.tools,
                max_tokens=spec.max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            ctx.reactive()
            request = ctx.render()
            self._note_prompt_parts(state, request)
            return spec.chat(
                spec.config,
                request.messages,
                system=request.system,
                tools=spec.tools,
                max_tokens=spec.max_tokens,
            )

    def _finish(self, state: RunState, transcript: Transcript, turn: Turn) -> RunOutcome | None:
        """终止路径委托给 stop 模块；None = 已补问，续轮。"""
        return decide(
            state,
            transcript,
            turn,
            max_blocks=self.spec.max_stop_blocks,
            emitted=self._emit,
        )

    def _emit(self, message: dict[str, Any]) -> None:
        if self.on_message is not None:
            self.on_message(message)

    def _note_prompt_parts(self, state: RunState, request: ComposedRequest) -> None:
        state.record_prompt_parts(
            system=request.system_chars,
            tools=len(json.dumps(self.spec.tools, ensure_ascii=False))
            if self.spec.tools
            else 0,
            messages=request.messages_chars,
        )
