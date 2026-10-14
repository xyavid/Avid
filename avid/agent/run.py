"""One run's scheduling: the round loop, its cancellation checkpoints and its single exit.

Everything else sits with its own subject — assembly and the model call (context), tool batches
(execution), termination (stop). Behaviour is pinned by the sequence-consistency cases in
tests/test_run.py.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..providers.client import Turn
from .context import ContextManager
from .events import RUN_STATUS, RunObserver
from .execution import execute_batch
from .hooks import BLOCK
from .spec import RunSpec
from .state import RunState
from .stop import STOP_PROMPT_BLOCKED, RunOutcome, decide, denial_halt
from .transcript import Transcript

if TYPE_CHECKING:
    from ..security.permission import AskUser

logger = logging.getLogger("avid.agent.run")


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
        # Callback after the history is replaced (summary message, kept tail size); the CLI wires it
        # to the session cursor, and without it compaction stays in memory and reruns next time.
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
            # A bare state for callers that passed none (tests, benchmarks). Permission, sandbox,
            # workspace root and the plan sinks are the caller's to build and pass in.
            state = RunState.for_run(ask=self.ask, observer=self.on_event)
        # A caller-built state has no probed window yet (probing happens in resolve), so fill it in.
        if state.context_window is None:
            state.context_window = spec.config.context_window

        ctx = ContextManager.from_spec(
            spec,
            transcript=Transcript(self.messages),
            state=state,
            on_compaction=self.on_compaction,
        )
        transcript = ctx.transcript

        trigger = _submit_input(transcript, state, spec.tool_names)
        if trigger is None:
            # UserPromptSubmit blocked: the run ends before it starts, no model round to ask again
            return RunOutcome(text="", reason=STOP_PROMPT_BLOCKED)
        index, injected = trigger
        self._emit(transcript.as_messages()[index])

        # Steers picked up at the "model is done" gate wait for the next round's delivery point.
        pending_steers: list[dict[str, Any]] = []

        for round_index in itertools.count(1):
            state.round = round_index
            state.check_cancelled()  # cancellation checkpoint 1: before the round starts
            # Sole delivery point for steers: before compose() each step, where appending is safe.
            for message in [*pending_steers, *self._take_steers(state)]:
                transcript.append(message)
                self._emit(message)
            pending_steers = []
            state.emit(RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

            request = ctx.compose(injected=injected)
            turn = ctx.call(request)

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
                # Before finishing: a steer continues the loop so it goes out next round.
                pending_steers = self._take_steers(state)
                if pending_steers:
                    continue
                final = self._finish(state, transcript, turn)
                if final is not None:
                    return final
                continue

            state.check_cancelled()  # cancellation checkpoint 2: before the tool batch
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
                # Denial halt: a fresh steer may redirect the approach, so let it speak first.
                pending_steers = self._take_steers(state)
                if pending_steers:
                    continue
                return denial_halt(state, transcript, emitted=self._emit)
        raise AssertionError("轮次循环没有正常出口")  # pragma: no cover

    def _finish(self, state: RunState, transcript: Transcript, turn: Turn) -> RunOutcome | None:
        """Termination is delegated to the stop module; None means a nudge was sent, so continue."""
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

    def _take_steers(self, state: RunState) -> list[dict[str, Any]]:
        """Pull steers to merge into the next step; empty list when there is no channel."""
        probe = state.steers
        if probe is None:
            return []
        return [dict(message) for message in probe()]
