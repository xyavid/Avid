"""The hook surface: a registry for the loop's four events, filled only by extra configuration.

The body registers nothing here — the permission gate, the output budget, the repeat reminder and
the run summary are plain code in guards.py — so what a run enforces never depends on what someone
put in this registry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("avid.agent.hooks")

# The four hook event names; anything else raises, because a typo would silently disable a gate.
EVENTS: tuple[str, ...] = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

# Callback signature: return "block" to veto the step, or None to let it pass.
Hook = Callable[[dict[str, Any]], "str | None"]

ALLOW = "allow"
BLOCK = "block"


class HookRegistry:
    """Callbacks for one run; isolating them lets a test or subagent replace the whole set."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = {event: [] for event in EVENTS}

    def register(self, event: str, hook: Hook | None = None):
        """Register a callback, or act as a decorator; an unknown event name raises."""
        if event not in EVENTS:
            raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")
        if hook is None:
            return lambda fn: self.register(event, fn)
        self._hooks.setdefault(event, []).append(hook)
        return hook

    def registered(self, event: str) -> list[Hook]:
        """Return the callbacks for one event in registration order, for diagnostics and tests."""
        return list(self._hooks.get(event, []))

    def trigger(self, event: str, context: dict[str, Any]) -> str:
        """Run every callback for an event in order and report whether any of them blocked."""
        if event not in EVENTS:
            raise ValueError(f"未知事件名 {event!r}；可用：{'、'.join(EVENTS)}")

        # The event name travels in the context so one callback can serve both phases.
        context["event"] = event
        blocked = False

        # Every callback runs even after one blocks, so an audit callback still sees the call.
        for hook in list(self._hooks.get(event, [])):
            name = getattr(hook, "__name__", repr(hook))
            try:
                result = hook(context)
            except Exception:
                # A raising callback counts as a block, so a broken gate fails closed.
                logger.exception("回调 %s 在 %s 抛出异常，按 block 处理", name, event)
                blocked = True
                continue
            if result == BLOCK:
                logger.info("%s 被 %s 拦截", event, name)
                blocked = True

        return BLOCK if blocked else ALLOW

    def copy(self) -> "HookRegistry":
        """Return an independently extendable copy; the callback objects themselves stay shared."""
        clone = HookRegistry()
        for event, callbacks in self._hooks.items():
            clone._hooks[event] = list(callbacks)
        return clone


#: Process-level registry, empty on purpose: the body registers no hooks of its own.
DEFAULT_HOOKS = HookRegistry()
