"""One run's assembly: the single place that turns a host's facts into a runnable Run.

Every host — each CLI path, the web registry — must produce the same three things: the state that
owns the machine facts, the tool table, and the spec that owns the model and that table. Doing it
here is what stops a path from quietly running with a different tool table, without a plan sink, or
without its checkpoint; the session, recorder and event lifecycles stay with the hosts that own
them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..agent.context import ContextBudget
from ..agent.events import RunObserver
from ..agent.hooks import HookRegistry
from ..agent.run import Run
from ..agent.spec import RunSpec
from ..agent.state import RunState, SteerProbe
from ..agent.stop import RunOutcome
from ..agent.tools import ToolImpl, build_toolset, without_writers
from ..agent.tools.mcp import McpManager
from ..providers.client import Turn, chat_completion
from ..providers.config import Config
from ..session.recorder import SessionRecorder, stored_plan

if TYPE_CHECKING:
    from ..agent.state import Checkpointer
    from ..security.permission import AskQuestion, AskUser, RunSecurity

logger = logging.getLogger("avid.services.assembly")


@dataclass
class RunAssembly:
    """One assembled run: run it, then close it; ``spec`` and ``state`` stay reachable."""

    messages: list[dict[str, Any]]
    spec: RunSpec
    state: RunState
    on_message: Callable[[dict[str, Any]], Any] | None = None
    on_compaction: Callable[[dict[str, Any], int], None] | None = None

    def run(self) -> RunOutcome:
        """The one place a run is constructed; every host reaches the loop through it."""
        return Run(
            self.messages,
            self.spec,
            state=self.state,
            on_message=self.on_message,
            on_compaction=self.on_compaction,
        ).run()

    def close(self) -> None:
        """MCP servers live exactly as long as the run does; idempotent."""
        self.state.close_mcp()


def assemble(
    *,
    config: Config,
    messages: list[dict[str, Any]],
    workspace_root: str | None = None,
    recorder: SessionRecorder | None = None,
    # Machine facts, all owned by RunState; unset means this host has none of it.
    auto_approve: bool = False,
    full: bool = False,
    permission_mode: str | None = None,
    security: "RunSecurity | None" = None,
    scratch: bool = False,
    hooks: HookRegistry | None = None,
    model_ref: str | None = None,
    effort: str | None = None,
    context_window: int | None = None,
    ask: "AskUser | None" = None,
    question: "AskQuestion | None" = None,
    steers: SteerProbe | None = None,
    observer: RunObserver | None = None,
    plan: list[Any] | None = None,
    checkpoint: "Checkpointer | None" = None,
    # The run's own shape, all owned by RunSpec.
    chat: Callable[..., Turn] | None = None,
    summarize: Callable[..., Turn] | None = None,
    instructions: str | None = None,
    budget: ContextBudget | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    # Callbacks the host already wrapped (index notification, event tagging, persistence).
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    on_compaction: Callable[[dict[str, Any], int], None] | None = None,
    # Where MCP start failures go: the CLI shows them to the user, the web logs them.
    warn: Callable[[str], None] | None = None,
) -> RunAssembly:
    """Build one run. An injected ``tools`` table means no MCP at all (tests, benchmarks); a
    scratch run drops writers from either table, and a ``recorder`` also wires the plan and the
    compaction cursor, since both belong to that branch's single writer.
    """
    on_plan: Callable[[list[dict[str, str]]], None] | None = None
    if recorder is not None:
        if plan is None:
            plan = stored_plan(recorder.session, recorder.branch)
        on_plan = recorder.record_plan
        on_compaction = on_compaction or recorder.record_compaction

    state = RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        question=question,
        steers=steers,
        observer=observer,
        permission_mode=permission_mode,
        security=security,
        workspace_root=workspace_root,
        hooks=hooks,
        context_window=context_window,
        full=full,
        scratch=scratch,
        model_ref=model_ref,
        effort=effort,
        plan=plan,
        on_plan=on_plan,
    )
    state.checkpoint = checkpoint

    if (tools is None) != (registry is None):
        raise ValueError(
            "tools 与 registry 必须一起给或一起不给：半个工具表只会让每次调用回「未知工具」"
        )

    try:
        if tools is None:
            # Nothing injected: the table is the built-ins plus this run's MCP servers.
            manager = McpManager(workspace_root)
            state.mcp = manager
            report = warn or logger.warning
            for warning in manager.start_all():
                report(warning)
            schemas, impls = build_toolset(state)
        else:
            assert registry is not None  # the pair guard above
            schemas, impls = tools, registry
            if scratch:
                schemas, impls = without_writers(schemas, impls)
        spec = RunSpec.resolve(
            config=config,
            chat=chat or chat_completion,
            summarize=summarize,
            instructions=instructions,
            budget=budget,
            tools=schemas,
            registry=impls,
        )
    except BaseException:
        # Nothing receives the run when assembly fails, so servers started above would outlive it:
        # the caller has no state to close, which is exactly why the closure belongs here.
        with suppress(Exception):
            state.close_mcp()
        raise
    return RunAssembly(
        messages=messages,
        spec=spec,
        state=state,
        on_message=on_message,
        on_compaction=on_compaction,
    )
