"""Derives the tool tables every consumer reads from the single declaration point.

Each tool's facts live with its implementation in ``registry.py``, so this module only
projects them.

"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .registry import ToolSpec, ensure_loaded, specs

# Parameters are relaxed to ``...``: most tools take (args), the few that need run state
# take (args, *, state).
ToolImpl = Callable[..., Any]

ensure_loaded()

TOOLS: list[dict[str, Any]] = [spec.schema() for spec in specs()]

TOOL_IMPLS: dict[str, ToolImpl] = {spec.name: spec.impl for spec in specs()}

# The subagent tool set, with subagent itself removed so recursion is impossible.
SUB_TOOLS: list[dict[str, Any]] = [
    item for item in TOOLS if item["function"]["name"] != "subagent"
]
SUB_HANDLERS: dict[str, ToolImpl] = {
    name: impl for name, impl in TOOL_IMPLS.items() if name != "subagent"
}


def build_toolset(state: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """Returns the schemas and handlers for one run: the built-ins plus any MCP tools.

    The MCP tools come from ``state.mcp`` and follow the built-ins in name and order.
    """
    schemas = list(TOOLS)
    impls = dict(TOOL_IMPLS)
    manager = getattr(state, "mcp", None)
    if manager is not None:
        extra_schemas, extra_impls = manager.toolset()
        schemas.extend(extra_schemas)
        impls.update(extra_impls)
    return schemas, impls


__all__ = [
    "SUB_HANDLERS",
    "SUB_TOOLS",
    "TOOLS",
    "TOOL_IMPLS",
    "ToolImpl",
    "ToolSpec",
    "build_toolset",
    "specs",
]
