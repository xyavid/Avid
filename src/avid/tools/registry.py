"""Declares each tool once and derives every tool table from that declaration.

A tool's schema, implementation and concurrency class all live next to the implementation
in one ``@tool`` decorator.

"""


from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .schemas import tool as _envelope

#: Parameters are relaxed to ``...``: most tools take (args), the few needing run state
#: take (args, *, state).
ToolImpl = Callable[..., Any]


@dataclass(frozen=True)
class ToolSpec:
    """The complete declaration of one tool."""

    name: str
    description: str
    parameters: dict[str, Any]
    impl: ToolImpl
    #: "safe" = read-only and may share a segment; "exclusive" = a barrier that runs alone.
    concurrency: str
    #: Whether the implementation accepts ``state=``; inferred from the signature by default.
    stateful: bool

    def schema(self) -> dict[str, Any]:
        """Returns the function-calling envelope handed to the model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_SPECS: list[ToolSpec] = []
_LOADED = False


def _register(spec: ToolSpec) -> None:
    if any(item.name == spec.name for item in _SPECS):
        raise ValueError(f"工具 {spec.name} 重复登记")
    _SPECS.append(spec)


def tool(
    *,
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: tuple[str, ...] = (),
    concurrency: str,
    stateful: bool | None = None,
) -> Callable[[ToolImpl], ToolImpl]:
    """Declares one tool, with its schema next to its implementation.

    ``stateful`` defaults to inspecting the signature, where needing run state is already
    visible.
    """
    if concurrency not in ("safe", "exclusive"):
        raise TypeError(
            f"工具 {name} 必须显式声明 concurrency='safe' 或 'exclusive'（拿不准就 exclusive）"
        )

    def decorate(fn: ToolImpl) -> ToolImpl:
        inferred = stateful
        if inferred is None:
            inferred = "state" in inspect.signature(fn).parameters
        envelope = _envelope(name, description, properties, required)
        _register(
            ToolSpec(
                name=name,
                description=description,
                parameters=envelope["function"]["parameters"],
                impl=fn,
                concurrency=concurrency,
                stateful=bool(inferred),
            )
        )
        return fn

    return decorate


def ensure_loaded() -> None:
    """Imports every tool module to trigger registration; idempotent, and order defines TOOLS."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from ..policy import todo  # noqa: F401  (todo_write lives in the policy layer)
    from . import files, shell, skill, subagent, web_search  # noqa: F401


def specs() -> tuple[ToolSpec, ...]:
    ensure_loaded()
    return tuple(_SPECS)
