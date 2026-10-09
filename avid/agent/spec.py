"""RunSpec: one run's read-only input.

resolve() is the only construction entry — model config resolution, window probing, the tool table
and schemas, and the parallel limit all land there; mutable run-time state (RunState) is not part
of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..providers.byok import resolve_chat
from ..providers.client import DEFAULT_MAX_TOKENS, Turn, chat_completion, fetch_context_length
from ..providers.config import Config
from .context import ContextBudget
from .hooks import HookRegistry
from .state import MAX_CONSECUTIVE_DENIALS
from .stop import MAX_STOP_BLOCKS
from .tools import TOOL_IMPLS, TOOLS, ToolImpl

if TYPE_CHECKING:
    from ..security.permission import ApprovalLedger, RunSecurity


@dataclass(frozen=True)
class RunSpec:
    config: Config
    chat: Callable[..., Turn]
    tools: list[dict[str, Any]]
    registry: dict[str, ToolImpl]
    schemas: dict[str, dict[str, Any]]
    tool_names: list[str]
    instructions: str | None
    summarize: Callable[..., Turn] | None
    budget: ContextBudget | None
    auto_approve: bool
    permission_mode: str | None
    ledger: "ApprovalLedger | None"
    security: "RunSecurity | None"
    workspace_root: str | None
    hooks: HookRegistry | None
    max_tokens: int | None
    max_stop_blocks: int
    max_consecutive_denials: int
    parallel_limit: int

    @classmethod
    def resolve(
        cls,
        *,
        config: Config | None = None,
        chat: Callable[..., Turn] = chat_completion,
        tools: list[dict[str, Any]] | None = None,
        registry: dict[str, ToolImpl] | None = None,
        instructions: str | None = None,
        summarize: Callable[..., Turn] | None = None,
        budget: ContextBudget | None = None,
        auto_approve: bool = False,
        permission_mode: str | None = None,
        ledger: "ApprovalLedger | None" = None,
        security: "RunSecurity | None" = None,
        workspace_root: str | None = None,
        hooks: HookRegistry | None = None,
        max_tokens: int | None = DEFAULT_MAX_TOKENS,
        max_stop_blocks: int = MAX_STOP_BLOCKS,
        max_consecutive_denials: int = MAX_CONSECUTIVE_DENIALS,
        max_parallel_tools: int | None = None,
    ) -> RunSpec:
        config = config or resolve_chat()
        if config.context_window is None:
            probed = fetch_context_length(config)
            if probed:
                config = replace(config, context_window=probed)
        tools = TOOLS if tools is None else tools
        registry = TOOL_IMPLS if registry is None else registry
        return cls(
            config=config,
            chat=chat,
            tools=tools,
            registry=registry,
            schemas={
                str(item["function"]["name"]): item["function"]["parameters"]
                for item in tools
            },
            tool_names=[str(item["function"]["name"]) for item in tools],
            instructions=instructions,
            summarize=summarize,
            budget=budget,
            auto_approve=auto_approve,
            permission_mode=permission_mode,
            ledger=ledger,
            security=security,
            workspace_root=workspace_root,
            hooks=hooks,
            max_tokens=max_tokens,
            max_stop_blocks=max_stop_blocks,
            max_consecutive_denials=max_consecutive_denials,
            parallel_limit=(
                config.max_parallel_tool_calls
                if max_parallel_tools is None
                else max_parallel_tools
            ),
        )
