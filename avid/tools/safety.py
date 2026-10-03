"""Answers which tool calls in one batch run concurrently.

Concurrency-safe calls share an execution segment; an exclusive call is a barrier that runs
alone and an unknown name counts as exclusive.

"""


from __future__ import annotations

from .registry import specs

#: Read-only tools: any number of these may overlap inside one segment.
CONCURRENCY_SAFE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "safe"
)

#: Tools that write, spawn processes or touch shared state: each runs alone as a barrier.
EXCLUSIVE: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.concurrency == "exclusive"
)


def is_concurrency_safe(name: str) -> bool:
    """Reports whether a tool may overlap with its segment peers; unknown names are unsafe."""
    return name in CONCURRENCY_SAFE


__all__ = ["CONCURRENCY_SAFE", "EXCLUSIVE", "is_concurrency_safe"]
