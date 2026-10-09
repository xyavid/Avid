"""Derives every consumer-facing tool table from the single declaration point in ``registry.py``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .registry import ToolSpec, ensure_loaded, specs

# Parameters are relaxed to ``...``: most tools take (args), a few take (args, *, state).
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


def readonly_names() -> frozenset[str]:
    """The tool names without write capability; a scratch conversation trims its tables by them."""
    return frozenset(spec.name for spec in specs() if not spec.writes)


def without_writers(
    schemas: list[dict[str, Any]], impls: dict[str, ToolImpl]
) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """Drops writers from any tool table; the injected and MCP tables both pass through here."""
    names = readonly_names()
    return (
        [item for item in schemas if item["function"]["name"] in names],
        {name: impl for name, impl in impls.items() if name in names},
    )


def build_toolset(state: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """Returns one run's schemas and handlers — built-ins plus ``state.mcp`` — dropping every
    writer in a scratch conversation because external tools' write capability is unknown.
    """
    schemas = list(TOOLS)
    impls = dict(TOOL_IMPLS)
    manager = getattr(state, "mcp", None)
    if manager is not None:
        extra_schemas, extra_impls = manager.toolset()
        schemas.extend(extra_schemas)
        impls.update(extra_impls)
    if getattr(state, "scratch", False):
        return without_writers(schemas, impls)
    return schemas, impls


__all__ = [
    "SUB_HANDLERS",
    "SUB_TOOLS",
    "TOOLS",
    "TOOL_IMPLS",
    "ToolImpl",
    "ToolSpec",
    "build_toolset",
    "readonly_names",
    "specs",
    "without_writers",
]
