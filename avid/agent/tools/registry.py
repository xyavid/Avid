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
    #: "safe" = read-only and may share a segment; "exclusive" = a barrier that runs alone;
    #: "conditional" = decided per call by ``assess`` (may only be downgraded to safe).
    concurrency: str
    #: Whether the implementation accepts ``state=``; inferred from the signature by default.
    stateful: bool
    #: Whether this tool writes the user's files; scratch conversations drop it. Only direct
    #: disk writes count: bash writes through the sandbox, which a read-only sandbox blocks.
    writes: bool = False
    #: Per-call concurrency verdict for "conditional" tools: (arguments, state) -> "safe" |
    #: "exclusive". Anything but "safe", a raise, or a missing assessor means exclusive.
    assess: Callable[[dict[str, Any], Any], str] | None = None

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
    writes: bool = False,
    assess: Callable[[dict[str, Any], Any], str] | None = None,
) -> Callable[[ToolImpl], ToolImpl]:
    """Declares one tool with its schema; ``stateful`` defaults to the signature, where needing
    run state is already visible."""
    if concurrency not in ("safe", "exclusive", "conditional"):
        raise TypeError(
            f"工具 {name} 必须显式声明 concurrency='safe'、'exclusive' 或 'conditional'"
            "（拿不准就 exclusive）"
        )
    if concurrency == "conditional" and assess is None:
        raise TypeError(f"工具 {name} 声明了 conditional，就必须给 assess(arguments, state)")
    if concurrency != "conditional" and assess is not None:
        raise TypeError(f"工具 {name} 只在自己是 conditional 时才给 assess")
    if writes and concurrency == "conditional":
        raise TypeError(
            f"工具 {name} 会写文件，不能声明 conditional：降级是一处误判面，不开在写路径上"
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
                writes=writes,
                assess=assess,
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
    from .. import todo  # noqa: F401  (todo_write lives in the agent layer)
    from . import files, interaction, search, shell, skill, subagent  # noqa: F401


def by_name(name: str) -> ToolSpec | None:
    """The declaration for one tool, or None when it never registered — callers treat that as
    unsafe."""
    ensure_loaded()
    for item in _SPECS:
        if item.name == name:
            return item
    return None


def specs() -> tuple[ToolSpec, ...]:
    ensure_loaded()
    return tuple(_SPECS)
