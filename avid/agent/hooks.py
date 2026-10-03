"""Hook registry for the four loop events, plus the default callbacks behind the gates."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from ..security.permission import (
    DEFAULT_MODE,
    always_allow,
    brokerize,
    decide,
)
from .compaction import spill

logger = logging.getLogger("avid.agent.hooks")

# Argument keys whose values are masked before they can reach the logs.
_SECRET_KEYS = ("token", "api_key", "apikey", "authorization", "password", "secret", "key")
# Inline credential forms: name=value and name: value, with an optional scheme word first.
_SECRET_IN_TEXT = re.compile(
    r"(?i)((?:authorization|token|api[_-]?key|password|secret)\s*[=:]\s*)"
    r"(?:bearer\s+)?\S+"
)
# Bare bearer tokens need their own pattern, since the one above requires a key name.
_SECRET_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
_BRIEF_LIMIT = 300  # characters kept from a log summary before it is truncated


def brief(arguments: Any, *, limit: int = _BRIEF_LIMIT) -> str:
    """Return a log-safe argument summary with credentials masked and the text truncated."""

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: (
                    "***"
                    if any(mark in str(key).lower() for mark in _SECRET_KEYS)
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, str):
            return _SECRET_IN_TEXT.sub(
                r"\1***", _SECRET_BEARER.sub(r"\1***", value)
            )
        return value

    # Masking is deliberately generous: the logs exist to locate problems, not to archive commands.
    text = json.dumps(redact(arguments), ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…（已截断）"

# The four hook event names; anything else raises, because a typo would silently disable a gate.
EVENTS: tuple[str, ...] = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")

# Callback signature: return "block" to veto the step, or None to let it pass.
Hook = Callable[[dict[str, Any]], "str | None"]

ALLOW = "allow"
BLOCK = "block"

# Cap on tool output entering the context; the overflow is spilled to disk as an excerpt.
MAX_TOOL_OUTPUT_CHARS = 8000


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


# Process-level default registry, used whenever a run does not pass its own.
DEFAULT_HOOKS = HookRegistry()


def permission_facts(
    name: str, arguments: dict[str, Any], root: str | None = None
) -> tuple[str | None, str | None]:
    """Return the first danger category and first outside path for a call, as a thin broker view."""
    action = brokerize(name, arguments, root=root)
    return action.danger, (action.outside[0] if action.outside else None)


def permission_hook(context: dict[str, Any]) -> str | None:
    """PreToolUse gate: broker the call, decide it, audit the verdict and explain any denial."""
    name = context.get("tool", "")
    arguments = context.get("arguments") or {}
    security = context.get("security")
    mode = security.mode if security is not None else (context.get("permission_mode") or DEFAULT_MODE)
    ladder = security.ladder if security is not None else None
    sandbox = security.sandbox if security is not None else None
    ledger = context.get("approval_ledger")

    action = brokerize(name, arguments, root=context.get("workspace_root"))
    # Auto-approve only swaps the answerer; it does not change which calls are questioned.
    answerer = always_allow if context.get("auto_approve") else context.get("ask")

    decision = decide(
        action,
        mode=mode,
        ladder=ladder,
        sandbox=sandbox,
        ledger=ledger,
        ask=answerer,
    )

    # Allows are audited as well, otherwise nothing records what this run actually let through.
    if security is not None:
        security.audit_write(
            "decision",
            tool=name,
            command=action.normalized or None,
            targets=list(action.targets),
            outside=list(action.outside),
            risks=list(action.risks),
            capabilities=sorted(action.capabilities),
            network=action.network or None,
            network_target=action.network_target if action.network else None,
            decision_type=decision.type,
            code=decision.code or None,
            operation=decision.operation or None,
            target=decision.target or None,
            verdict=decision.verdict,
            decision_kind=decision.kind or None,
            tier=decision.tier or None,
            answered_by=decision.answered_by or None,
            key=list(decision.key) if decision.key else None,
            reason=decision.reason or None,
        )

    if decision.allowed:
        return None

    # Denials carry a per-category reason, because a generic message makes the model retry blindly.
    context["denied_kind"] = decision.kind
    context["denied_type"] = decision.type
    context["denied_code"] = decision.code or None
    context["denied_reason"] = f"{name}：{decision.reason}"
    context["denied_content"] = decision.message
    return BLOCK


def log_hook(context: dict[str, Any]) -> str | None:
    """PreToolUse and PostToolUse logging of call start, call end and denials; never blocks."""
    tool = context.get("tool")
    if context.get("event") == "PreToolUse":
        logger.info("[hook] 调用 %s %s", tool, brief(context.get("arguments")))
    else:
        content = context.get("content") or ""
        suffix = "（已被截断）" if context.get("truncated") else ""
        logger.info("[hook] 返回 %s，%d 字符%s", tool, len(content), suffix)

    if context.get("denied_reason"):
        logger.info("[hook] 拒绝原因：%s", context["denied_reason"])
    return None


def large_output_hook(context: dict[str, Any]) -> str | None:
    """PostToolUse budget: keep head and tail excerpts of oversized output and spill the rest."""
    content = context.get("content")
    if not isinstance(content, str) or len(content) <= MAX_TOOL_OUTPUT_CHARS:
        return None

    original = len(content)
    path = spill(
        content,
        "tool-output",
        context.get("workspace_root"),
        str(context.get("run_tag") or ""),
    )
    if path is not None:
        notice = (
            f"\n…（hook 按上下文预算截断，原文 {original} 字符，已存至 {path}；"
            "需要时用 read_file 读回。以下为首尾节选）\n"
        )
    else:
        notice = f"\n…（hook 按上下文预算截断，原文 {original} 字符，上限 {MAX_TOOL_OUTPUT_CHARS}）"

    # A failed spill downgrades this to a head-only truncation; it never drops the result.
    if len(notice) >= MAX_TOOL_OUTPUT_CHARS:
        context["content"] = notice[:MAX_TOOL_OUTPUT_CHARS]
        context["truncated"] = True
        return None

    # The notice is charged to the same budget, otherwise the cap would be silently exceeded.
    keep = MAX_TOOL_OUTPUT_CHARS - len(notice)
    head = keep // 2
    context["content"] = content[:head] + notice + content[original - (keep - head) :]
    context["truncated"] = True
    return None


def repeat_call_hook(context: dict[str, Any]) -> str | None:
    """PostToolUse notice at the third and fifth identical call; it appends and never blocks."""
    tool = str(context.get("tool") or "")
    arguments = context.get("arguments")
    counts = context.get("repeat_calls")
    content = context.get("content")
    if not tool or arguments is None or not isinstance(counts, dict):
        return None

    # Normalized JSON makes the comparison independent of key order and formatting.
    key = (
        f"{tool}:"
        f"{json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)}"
    )
    # Concurrent calls bump the run's atomic counter; a direct caller falls back to a local dict.
    bump = context.get("bump_repeat")
    if callable(bump):
        times = bump(key)
    else:
        counts[key] = counts.get(key, 0) + 1
        times = counts[key]
    if times not in REPEAT_REMIND_AT or not isinstance(content, str):
        return None

    # The reminder is appended, so the previous output stays visible in full.
    context["content"] = content + (
        f"\n\n[重复调用提醒] 同一次调用（{tool} + 相同参数）已经重复 {times} 次。"
        "先看上一次的结果再决定：换一种做法，或者如果已经做完就收尾。"
    )
    return None


def summary_hook(context: dict[str, Any]) -> str | None:
    """Stop hook that logs the round and tool-usage summary; it does not block by default."""
    summary = (
        f"轮数={context.get('rounds', 0)} "
        f"工具调用={context.get('tool_calls', 0)} "
        f"拒绝={context.get('denials', 0)}"
    )
    context["summary"] = summary
    logger.info("[hook] 运行汇总：%s", summary)
    return None


# Reminder thresholds; the first two repetitions are ordinary retry behaviour.
REPEAT_REMIND_AT: tuple[int, ...] = (3, 5)

# Registration order matters: permission_hook must run first so log_hook sees the denial reason,
# and the repeat reminder must precede truncation so its text is charged to the output budget.
# UserPromptSubmit keeps no default callback; user hooks may use it for input injection.
DEFAULT_HOOKS.register("PreToolUse", permission_hook)
DEFAULT_HOOKS.register("PreToolUse", log_hook)
DEFAULT_HOOKS.register("PostToolUse", repeat_call_hook)
DEFAULT_HOOKS.register("PostToolUse", large_output_hook)
DEFAULT_HOOKS.register("PostToolUse", log_hook)
DEFAULT_HOOKS.register("Stop", summary_hook)
