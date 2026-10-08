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


def readonly_names() -> frozenset[str]:
    """工具名里不带写入能力的那一份（临时的只读对话按它摘表）。"""
    return frozenset(spec.name for spec in specs() if not spec.writes)


def without_writers(
    schemas: list[dict[str, Any]], impls: dict[str, ToolImpl]
) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """把任一工具表里的写入工具摘掉：注入表、MCP 表都过这一道。"""
    names = readonly_names()
    return (
        [item for item in schemas if item["function"]["name"] in names],
        {name: impl for name, impl in impls.items() if name in names},
    )


def build_toolset(state: Any | None = None) -> tuple[list[dict[str, Any]], dict[str, ToolImpl]]:
    """Returns the schemas and handlers for one run: the built-ins plus any MCP tools.

    The MCP tools come from ``state.mcp`` and follow the built-ins in name and order.
    临时对话（``state.scratch``）过 :func:`without_writers`：写入工具摘表，MCP 也不放开——
    外部工具的能力我们不知道，说不清是否只读的东西不进这张表。
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
