"""Runtime：agent 运行期——事件单点、hook 四事件、RunState、工具协议、装配与循环。

分节自上而下：events（事件名单点）→ hooks（四事件与默认回调）→
state（一次运行的全部可变状态）→ execution（工具调用协议）→
context_manager（上下文装配与五步压缩编排）→ loop（只表达调度顺序）。
"""

from __future__ import annotations

import itertools
import json
import logging
import platform
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args

from .ai.byok import resolve_chat
from .ai.client import (
    DEFAULT_MAX_TOKENS,
    PromptTooLongError,
    Turn,
    chat_completion,
    fetch_context_length,
)
from .ai.config import Config
from .ai.transcript import Transcript, message_chars
from .ai.usage import Usage, hit_ratio
from .policy import compaction as compact
from .policy import prompt
from .policy.compaction import CompactReport, spill
from .policy.permission import (
    APPROVAL_NONE,
    DEFAULT_MODE,
    ApprovalLedger,
    RunSecurity,
    always_allow,
    brokerize,
    build_run_security,
    decide,
    validate_mode,
)
from .policy.skills import SkillLoader, default_skills_dir
from .policy.todo import TodoList
from .tools import TOOL_IMPLS, TOOLS, ToolImpl, workspace
from .tools.registry import specs
from .tools.safety import is_concurrency_safe
from .tools.validate import bad_arguments, validate_arguments

if TYPE_CHECKING:
    from .policy.permission import ApprovalLedger, AskUser, RunSecurity
    from .tools.mcp import McpManager

logger = logging.getLogger("avid.runtime")

# ──────────────────────────── events ────────────────────────────

# Event name registry: the single source for event names, tiers and stream timing constants.

# Durable events: replayable, carry id and seq, and define reconnect catch-up granularity.
RUN_STARTED = "run_started"
USER_MESSAGE = "user_message"
ASSISTANT_MESSAGE = "assistant_message"
TOOL_RESULT_MESSAGE = "tool_result_message"
TOOL_CALL_STARTED = "tool_call_started"
TOOL_CALL_FINISHED = "tool_call_finished"
TOOL_CALL_DENIED = "tool_call_denied"
APPROVAL_REQUESTED = "approval_requested"
APPROVAL_RESOLVED = "approval_resolved"
CONTEXT_COMPACTED = "context_compacted"
TODO_REMINDER = "todo_reminder"
STOP_NUDGE = "stop_nudge"
RUN_FINISHED = "run_finished"
RUN_FAILED = "run_failed"
RUN_CANCELLED = "run_cancelled"
RESYNC = "resync"

# Transient events: state only, dropped on disconnect and never replayed.
RUN_STATUS = "run_status"

# Delta events: lossy fragments, delivered only when a subscriber opts in.
ASSISTANT_DELTA = "assistant_delta"
#: Reasoning-token delta: rendered in its own card and never merged into the reply body.
REASONING_DELTA = "reasoning_delta"

DURABLE_EVENT_TYPES: tuple[str, ...] = (
    RUN_STARTED,
    USER_MESSAGE,
    ASSISTANT_MESSAGE,
    TOOL_RESULT_MESSAGE,
    TOOL_CALL_STARTED,
    TOOL_CALL_FINISHED,
    TOOL_CALL_DENIED,
    APPROVAL_REQUESTED,
    APPROVAL_RESOLVED,
    CONTEXT_COMPACTED,
    TODO_REMINDER,
    STOP_NUDGE,
    RUN_FINISHED,
    RUN_FAILED,
    RUN_CANCELLED,
    RESYNC,
)

TRANSIENT_EVENT_TYPES: tuple[str, ...] = (RUN_STATUS,)

DELTA_EVENT_TYPES: tuple[str, ...] = (ASSISTANT_DELTA, REASONING_DELTA)

#: The only hand-written event-name list; the union below derives from it, so the two cannot drift.
EventType = Literal[
    "run_started",
    "user_message",
    "assistant_message",
    "tool_result_message",
    "tool_call_started",
    "tool_call_finished",
    "tool_call_denied",
    "approval_requested",
    "approval_resolved",
    "context_compacted",
    "todo_reminder",
    "stop_nudge",
    "run_finished",
    "run_failed",
    "run_cancelled",
    "resync",
    "run_status",
    "assistant_delta",
    "reasoning_delta",
]

#: Full event list in declaration order, which the frontend type union is compared against.
EVENT_TYPES: tuple[EventType, ...] = get_args(EventType)

#: Terminal events: the frontend cancels pending deltas when one arrives.
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {RUN_FINISHED, RUN_FAILED, RUN_CANCELLED}
)

DURABLE_SET: frozenset[str] = frozenset(DURABLE_EVENT_TYPES)

# Client-visible timing: the published heartbeat, the generator's heartbeat and the frontend
# timeout must all agree, otherwise the client and server disagree about a dead connection.

#: SSE heartbeat interval; after this silence the server sends a comment frame as a ping.
STREAM_HEARTBEAT_SECONDS = 15.0
#: Silence after which the client reconciles with the run registry instead of trusting the stream.
TERMINAL_FALLBACK_SECONDS = 30.0

def now_ms() -> int:
    """Event timestamp in milliseconds from the server clock; remote wall clocks never compare."""
    return int(time.time() * 1000)

@dataclass(frozen=True)
class RunEvent:
    """One run event; a seq of None means the event is outside the replay buffer."""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    seq: int | None = None
    ts: int = 0

    @property
    def durable(self) -> bool:
        """Whether this event can be replayed: it needs both a seq and a durable type."""
        return self.seq is not None and self.type in DURABLE_SET

def event(type: str, **data: Any) -> RunEvent:
    """Build a kernel-side event; run_id and seq stay empty for the svc registry to fill in."""
    if type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {type!r}；可用：{'、'.join(EVENT_TYPES)}")
    return RunEvent(type=type, data=data, ts=now_ms())

class RunObserver(Protocol):
    """Event observer; the svc implementation assigns seq, buffers and wakes subscribers."""

    def __call__(self, event: RunEvent) -> None: ...

__all__ = [
    "ASSISTANT_DELTA",
    "ASSISTANT_MESSAGE",
    "APPROVAL_REQUESTED",
    "APPROVAL_RESOLVED",
    "CONTEXT_COMPACTED",
    "DELTA_EVENT_TYPES",
    "DURABLE_EVENT_TYPES",
    "DURABLE_SET",
    "EVENT_TYPES",
    "EventType",
    "REASONING_DELTA",
    "RESYNC",
    "RUN_CANCELLED",
    "RUN_FAILED",
    "RUN_FINISHED",
    "RUN_STARTED",
    "RUN_STATUS",
    "STOP_NUDGE",
    "TERMINAL_EVENT_TYPES",
    "TODO_REMINDER",
    "TOOL_CALL_DENIED",
    "TOOL_CALL_FINISHED",
    "TOOL_CALL_STARTED",
    "TOOL_RESULT_MESSAGE",
    "TRANSIENT_EVENT_TYPES",
    "USER_MESSAGE",
    "RunEvent",
    "RunObserver",
    "event",
    "now_ms",
]


# ──────────────────────────── hooks ────────────────────────────

# Hook registry for the four loop events, plus the default callbacks behind the gates.


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


# ──────────────────────────── state ────────────────────────────

# Mutable state of one run: counters, one-shot flags, the usage ledger and run objects.

# Abort the run after this many consecutive denials; any allowed call resets the streak.
MAX_CONSECUTIVE_DENIALS = 5

def _split_context(
    tokens: int | None, chars: tuple[int, int, int] | None
) -> dict[str, int] | None:
    """Split real prompt_tokens by character share; the three parts sum exactly to the total."""
    if tokens is None or chars is None:
        return None
    total = sum(chars)
    if total <= 0:
        return None
    system, tools, _ = chars
    system_tokens = tokens * system // total
    tools_tokens = tokens * tools // total
    return {
        "system": system_tokens,
        "tools": tools_tokens,
        "messages": tokens - system_tokens - tools_tokens,
    }

@dataclass
class RunState:
    """All mutable state of one run; built by the loop and passed explicitly to every stage."""

    # Run-level switch that auto-answers approval prompts without widening what is questioned.
    auto_approve: bool = False

    # One of the manual/auto/full presets; the default is strictest so no caller inherits more.
    permission_mode: str = DEFAULT_MODE

    # Run security spec (axes, deny ladder, sandbox, audit), the one place tools read it from;
    # built in __post_init__ when the constructor was not given one.
    security: RunSecurity | None = None

    # Explicit full-access credential from the CLI or Web wiring; without it the run will not start.
    full_ack: bool = False
    # Who supplied the full-access grant, recorded so the audit trail can name the origin.
    grant_source: str = "cli"

    # "Approved once" ledger, one per run and in memory only; a subagent shares the parent's.
    ledger: ApprovalLedger = field(default_factory=ApprovalLedger)

    # Workspace root for this run; None defers to tools.workspace.WORKSPACE_ROOT, read at call time.
    workspace_root: str | None = None

    # Approval callback injection point; None falls back to the default stdin-based prompter.
    ask: AskUser | None = None

    # Event observer; None means this run has no subscriber, as on the CLI path.
    observer: RunObserver | None = None

    # Cancellation is set by another thread and only read at the loop's two checkpoints.
    cancelled: bool = False
    cancel_reason: str | None = None
    # External stop source for child runs: it returns a reason or None, and is still read
    # only at checkpoints.
    cancel_probe: "Callable[[], str | None] | None" = None

    # Round counter and the number of times a Stop hook has blocked exit so far.
    round: int = 0
    stop_blocks: int = 0

    # One-shot flags: each of these expensive steps may happen at most once per run.
    compacted: bool = False
    retried: bool = False

    # Statistics that end up in the Stop event, for hooks and tests.
    tool_calls: int = 0
    denials: int = 0
    # Current streak: how many calls in a row were denied since the last allowed one.
    denial_streak: int = 0
    compactions: int = 0
    # Cumulative total_tokens over all rounds, which is not context occupancy.
    tokens: int = 0
    # Subagent usage folded in: how many child batches were adopted and their token total.
    child_calls: int = 0
    child_tokens: int = 0

    # Usage of the most recent model call, including cached prompt read and write counts.
    last_usage: Usage | None = None
    # Model context window; None means the model is unknown and occupancy cannot be computed.
    context_window: int | None = None
    # Measured prompt_tokens of the first round after a compaction, which is what it actually saved.
    last_compaction_tokens: int | None = None
    # Which compaction step produced the last report, shown in the detail view.
    last_compaction_step: str | None = None
    # Set by mark_compacted, consumed by record_usage: a post-compaction reading is pending.
    compact_pending: bool = False
    # Character counts (system, tools, messages) recorded before the request; they only split the
    # real prompt_tokens, so this field stores facts and does no conversion.
    prompt_parts: tuple[int, int, int] | None = None

    # Repeat counter keyed by tool name plus normalized arguments; each user input starts fresh.
    repeat_calls: dict[str, int] = field(default_factory=dict)

    # Short per-run id in spill filenames, so two runs never overwrite each other's context files.
    run_tag: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    # Guards the read-modify-write counters below, which worker threads may update concurrently.
    _counters_lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    # Run-scoped runtime objects.
    todo: TodoList = field(default_factory=TodoList)
    skills: SkillLoader = field(default_factory=SkillLoader)
    # MCP tool manager assembled by the run entry point; None means this run exposes no MCP tools.
    mcp: "McpManager | None" = None
    # Hook registry owned by the run, reached through the module so replacing the default works.
    hooks: "HookRegistry" = field(
        default_factory=lambda: DEFAULT_HOOKS
    )

    def __post_init__(self) -> None:
        """Resolve the security spec once here, because tools read it from worker threads."""
        if self.security is None:
            self.security = build_run_security(
                mode=validate_mode(self.permission_mode),
                root=self.workspace_root,
                run_tag=self.run_tag,
                full_ack=self.full_ack,
                source=self.grant_source,
            )
        # security is authoritative and permission_mode is only its name, so both are kept in step.
        self.permission_mode = self.security.mode

    @classmethod
    def for_run(
        cls,
        *,
        auto_approve: bool = False,
        ask: AskUser | None = None,
        observer: RunObserver | None = None,
        permission_mode: str | None = None,
        ledger: ApprovalLedger | None = None,
        workspace_root: str | None = None,
        hooks: "HookRegistry | None" = None,
        context_window: int | None = None,
        security: RunSecurity | None = None,
        full_ack: bool = False,
        grant_source: str = "cli",
        home: str | None = None,
        audit_dir: str | None = None,
        audit_enabled: bool = True,
    ) -> "RunState":
        """Build a run state and rescan skills; a supplied security spec is reused as-is."""
        name = validate_mode(permission_mode or DEFAULT_MODE)
        built = security or build_run_security(
            mode=name,
            root=workspace_root,
            home=home,
            full_ack=full_ack,
            source=grant_source,
            audit_dir=audit_dir,
            audit_enabled=audit_enabled,
        )
        return cls(
            auto_approve=auto_approve,
            ask=ask,
            observer=observer,
            permission_mode=built.mode,
            security=built,
            full_ack=full_ack,
            grant_source=grant_source,
            ledger=ledger if ledger is not None else ApprovalLedger(),
            workspace_root=workspace_root,
            context_window=context_window,
            # The skill directory follows the workspace root and falls back to cwd.
            skills=SkillLoader(default_skills_dir(workspace_root)).scan(),
            hooks=hooks if hooks is not None else DEFAULT_HOOKS,
        )

    def outside_allowed(self, path: object, access: str = "ro") -> bool:
        """Report whether an outside path is already authorized; it never decides, only reads."""
        if self.security is not None and self.security.approval == APPROVAL_NONE:
            return True
        return self.ledger.outside_allowed(path, access)

    def sandbox_grants(self) -> tuple[tuple[str, str], ...]:
        """Outside paths already granted for this run, as (path, ro/rw) pairs for the sandbox."""
        return self.ledger.path_grants()

    def security_summary(self) -> dict[str, Any]:
        """Three-axis security snapshot carried into events and REST responses."""
        if self.security is None:  # pragma: no cover - __post_init__ guarantees a value
            return {}
        return self.security.summary()

    def emit(self, type: str, **data: Any) -> None:
        """Hand one step-level fact to the observer; without one this is a no-op."""
        if self.observer is not None:
            self.observer(event(type, **data))

    def cancel(self, reason: str | None = None) -> None:
        """Request cancellation; this only sets a flag, so the current step still finishes."""
        self.cancelled = True
        self.cancel_reason = reason or "cancelled"

    def close_mcp(self) -> None:
        """Close this run's MCP servers; idempotent and called from the entry point's finally."""
        if self.mcp is not None:
            self.mcp.close()
            self.mcp = None

    def check_cancelled(self) -> None:
        """Called at the loop's checkpoints; it raises RunCancelled when the run should stop."""
        if self.cancelled:

            raise RunCancelled(self.cancel_reason or "cancelled")
        if self.cancel_probe is not None:
            reason = self.cancel_probe()
            if reason:

                raise RunCancelled(reason)

    def note_tool_call(self) -> None:
        """Count one tool call; worker threads call it, so the counter is taken under the lock."""
        with self._counters_lock:
            self.tool_calls += 1

    def note_denial(self) -> None:
        """Count one denied call and extend the streak that MAX_CONSECUTIVE_DENIALS bounds."""
        with self._counters_lock:
            self.denials += 1
            self.denial_streak += 1

    def note_allowed(self) -> None:
        """Clear the denial streak; a tool's own error is progress and never extends it."""
        with self._counters_lock:
            self.denial_streak = 0

    def note_repeat(self, key: str) -> int:
        """Return the occurrence count for a key from one, read-modify-written under the lock."""
        with self._counters_lock:
            times = self.repeat_calls.get(key, 0) + 1
            self.repeat_calls[key] = times
            return times

    def snapshot(self) -> dict[str, int]:
        """Run statistics exposed to the Stop event."""
        return {
            "rounds": self.round,
            "tool_calls": self.tool_calls,
            "denials": self.denials,
            "denial_streak": self.denial_streak,
            "compactions": self.compactions,
        }

    def record_usage(self, usage: Usage) -> None:
        """Record one model call's usage and backfill the post-compaction reading if pending."""
        self.tokens += usage.total_tokens
        self.last_usage = usage
        if self.compact_pending:
            self.last_compaction_tokens = usage.prompt_tokens
            self.compact_pending = False

    def adopt_child_usage(self, child: "RunState") -> None:
        """Fold a subagent's usage in; timed-out children count, as their tokens were spent."""
        with self._counters_lock:
            self.child_calls += 1
            self.child_tokens += child.tokens
            self.tokens += child.tokens

    def mark_compacted(self, step: str) -> None:
        """Record one compaction and arm the pending flag that the next usage report consumes."""
        self.compactions += 1
        self.last_compaction_step = step
        self.compact_pending = True

    def record_prompt_parts(self, *, system: int, tools: int, messages: int) -> None:
        """Record per-part character counts; only the loop knows the system and tools parts."""
        self.prompt_parts = (max(0, system), max(0, tools), max(0, messages))

    def usage_report(self) -> dict[str, Any]:
        """One usage schema for events, REST and persistence; None always means no number."""
        usage = self.last_usage
        if usage is not None and usage.prompt_tokens <= 0 and usage.total_tokens <= 0:
            # All zeros mean the endpoint reported no usage at all, not that zero tokens were used.
            usage = None
        prompt = None if usage is None else usage.prompt_tokens
        window = self.context_window
        return {
            "context": {
                "tokens": prompt,
                "window": window,
                "utilization": (
                    prompt / window if prompt is not None and window else None
                ),
                "parts": _split_context(prompt, self.prompt_parts),
            },
            "cache": {
                "read_tokens": None if usage is None else usage.cache_read_tokens,
                "write_tokens": None if usage is None else usage.cache_write_tokens,
                "hit_ratio": None if usage is None else hit_ratio(usage),
            },
            "compaction": {
                "count": self.compactions,
                "last_compaction_tokens": self.last_compaction_tokens,
                "last_step": self.last_compaction_step,
            },
            "subagent": {"calls": self.child_calls, "tokens": self.child_tokens},
        }


# ──────────────────────────── execution ────────────────────────────

# Tool execution: parse arguments, gate the call, run it and report the outcome as text.


# Fallback text when a blocking hook does not set a more useful denied_content of its own.
DENIED_CONTENT = "Permission denied."
# Text returned when PostToolUse blocks a result, so the model does not read it as empty output.
POST_BLOCKED_CONTENT = "错误：工具结果被 PostToolUse hook 拦截，内容未进入上下文。"
# A real "not executed" answer is required: every declared call needs one response, otherwise the
# assistant message keeps an unanswered call and the transcript becomes structurally invalid.
CANCELLED_CONTENT = "错误：运行已取消，本次调用未执行。"

# Tools needing the run state, derived from the registry instead of a hand-maintained list.
STATEFUL_TOOLS: frozenset[str] = frozenset(
    spec.name for spec in specs() if spec.stateful
)

@dataclass(frozen=True)
class ToolOutcome:
    """One tool call id paired with the content handed back to the model."""

    tool_call_id: str
    content: str

def _as_text(value: Any) -> str:
    """Render a result as text, JSON-encoding anything that is not already a string."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)

def execute_one(
    name: str,
    raw_arguments: str,
    registry: dict[str, ToolImpl],
    *,
    state: RunState,
    round_index: int,
    tool_call_id: str = "",
    parameters: dict[str, Any] | None = None,
    parallel: int = 1,
) -> str:
    """Run one tool call and return text for the model; failures return text rather than raising."""
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return bad_arguments(f"参数不是合法 JSON（{exc}）")

    if not isinstance(arguments, dict):
        return bad_arguments("参数必须是 JSON 对象")

    # Argument and protocol failures return without events; only policy outcomes are observable.
    impl = registry.get(name)
    if impl is None:
        return f"未知工具：{name}"

    if parameters is not None:
        # Validation runs against the schema node sent to the model, so the two cannot disagree.
        problem = validate_arguments(parameters, arguments)
        if problem is not None:
            return problem

    state.note_tool_call()
    started_at = time.monotonic()

    before: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "tool_call_id": tool_call_id,
        "auto_approve": state.auto_approve,
        # Run-level facts the permission layer reads; the workspace root is resolved at call time.
        "permission_mode": state.permission_mode,
        # The security spec travels as data, which is why this module imports nothing from policy.
        "security": state.security,
        "approval_ledger": state.ledger,
        "workspace_root": state.workspace_root or str(workspace.WORKSPACE_ROOT),
        # The approval callback is injected here so hooks never read stdin themselves.
        "ask": state.ask,
    }
    # parallel reports how many calls share this segment; it changes no execution semantics.
    state.emit(
        TOOL_CALL_STARTED,
        tool=name,
        arguments=arguments,
        round=round_index,
        tool_call_id=tool_call_id,
        parallel=parallel,
    )
    if state.hooks.trigger("PreToolUse", before) == BLOCK:
        state.note_denial()
        logger.info("  ✗ 已拦截 %s", name)
        state.emit(
            TOOL_CALL_DENIED,
            tool=name,
            arguments=arguments,
            round=round_index,
            tool_call_id=tool_call_id,
            kind=before.get("denied_kind") or "user",
            reason=before.get("denied_reason") or "",
            parallel=parallel,
        )
        # Whichever hook blocked the call decides the message; the fallback applies only
        # when none is set.
        return str(before.get("denied_content") or DENIED_CONTENT)

    # This call passed the gate, so the denial streak resets; a failing tool is not a denial.
    state.note_allowed()

    try:
        if name in STATEFUL_TOOLS:
            content = _as_text(impl(arguments, state=state))
        else:
            content = _as_text(impl(arguments))
    except Exception as exc:  # A tool failure goes back to the model and never breaks the loop
        content = (
            f"工具执行失败：{name}（{exc}）；"
            "不要用同样的参数重复调用，先检查参数与环境。"
        )

    after: dict[str, Any] = {
        "tool": name,
        "arguments": arguments,
        "round": round_index,
        "tool_call_id": tool_call_id,
        "content": content,
        "truncated": False,
        # Spills must land in this run's workspace and carry the run tag, or runs collide.
        "workspace_root": state.workspace_root or str(workspace.WORKSPACE_ROOT),
        "run_tag": state.run_tag,
        # Repeat counting lives on the run state; the next round sees the updated counts.
        "repeat_calls": state.repeat_calls,
        # PostToolUse may run concurrently, so counting goes through the atomic increment.
        "bump_repeat": state.note_repeat,
    }
    if state.hooks.trigger("PostToolUse", after) == BLOCK:
        # A blocked PostToolUse keeps the tool's real execution but swaps what enters the context.
        after["content"] = str(after.get("denied_content") or POST_BLOCKED_CONTENT)
        after["blocked"] = True
        logger.info("  ✗ 结果被 PostToolUse 拦截 %s", name)
    final = str(after["content"])
    state.emit(
        TOOL_CALL_FINISHED,
        tool=name,
        arguments=arguments,
        round=round_index,
        tool_call_id=tool_call_id,
        content=final,
        truncated=bool(after.get("truncated")),
        duration_ms=int((time.monotonic() - started_at) * 1000),
        parallel=parallel,
    )
    return final

def _call_name(call: dict[str, Any]) -> str:
    return str((call.get("function") or {}).get("name", ""))

def plan_segments(
    tool_calls: list[dict[str, Any]], max_parallel: int
) -> list[list[int]]:
    """Split a batch into ordered segments: safe calls share one, exclusive calls stand alone."""
    segments: list[list[int]] = []
    current: list[int] = []
    for index, call in enumerate(tool_calls):
        if max_parallel > 1 and is_concurrency_safe(_call_name(call)):
            current.append(index)
            continue
        if current:
            segments.append(current)
            current = []
        segments.append([index])
    if current:
        segments.append(current)
    # Cross-call data dependencies cannot be expressed in one assistant batch, so only category
    # order is enforced: a write splits the batch, which keeps earlier reads ahead of it.
    return segments

def execute_batch(
    tool_calls: list[dict[str, Any]],
    *,
    state: RunState,
    registry: dict[str, ToolImpl],
    round_index: int = 0,
    schemas: dict[str, dict[str, Any]] | None = None,
    max_parallel: int = 1,
) -> list[ToolOutcome]:
    """Execute a batch and return outcomes in assistant source order, never completion order."""
    limit = max(1, int(max_parallel))
    outcomes: list[ToolOutcome | None] = [None] * len(tool_calls)
    schemas = schemas or {}

    def run(index: int, *, width: int) -> str:
        # A segment is dispatched as a whole, so cancellation can land while a call is still queued.
        if state.cancelled:
            return CANCELLED_CONTENT
        call = tool_calls[index]
        name = _call_name(call)
        raw_arguments = (call.get("function") or {}).get("arguments") or "{}"
        logger.info("  → %s %s", name, brief(raw_arguments))
        try:
            content = execute_one(
                name,
                raw_arguments,
                registry,
                state=state,
                round_index=round_index,
                tool_call_id=str(call.get("id", "")),
                parameters=schemas.get(name),
                parallel=width,
            )
        except Exception as exc:  # Execution must not leak an exception to its batch siblings
            logger.exception("工具调用 %s 抛出未预期异常，按失败回传", name)
            content = f"工具执行失败：{name}（{exc}）；不要用同样的参数重复调用。"
        logger.info("  ← %s 字符", len(content))
        return content

    def store(index: int, content: str) -> None:
        outcomes[index] = ToolOutcome(
            tool_call_id=str(tool_calls[index].get("id", "")), content=content
        )

    for segment in plan_segments(tool_calls, limit):
        if state.cancelled:
            # Nothing is dispatched and the rest answer "not executed", keeping the batch complete.
            for index in segment:
                store(index, CANCELLED_CONTENT)
            continue
        if len(segment) == 1:
            store(segment[0], run(segment[0], width=1))
            continue
        width = min(len(segment), limit)
        logger.info("并行执行 %d 个调用（并发上限 %d）", len(segment), width)
        with ThreadPoolExecutor(max_workers=width) as pool:
            # partial rather than a lambda: a closure would capture the segment loop variable.
            results = list(pool.map(partial(run, width=width), segment))
        for index, content in zip(segment, results, strict=True):
            store(index, content)

    # Segments partition the batch, so every index must have received an outcome.
    missing = [index for index, item in enumerate(outcomes) if item is None]
    if missing:  # pragma: no cover - a correct partition cannot leave a gap
        raise AssertionError(f"批内调用 {missing} 没有结果，段划分不完整")
    return [item for item in outcomes if item is not None]


# ──────────────────────────── context_manager ────────────────────────────

# Context assembly and compaction: the single place deciding what the model sees each round.


#: Placement inside the system prompt, frozen on the first round so the provider prefix cache holds.
SYSTEM = "system"
TAIL = "tail"

#: Built-in block kinds; a new kind needs no change here, since register_source takes any string.
INSTRUCTIONS = "instructions"  # fixed instructions from the caller, or the default text
ENVIRONMENT = "environment"  # working directory plus the available tool names
BOOTSTRAP = "bootstrap"  # the workspace AGENTS.md conventions; a missing file means no block
SKILL_ALWAYS = "skill_always"  # full text of always-marked skills, capped per skill and in total
SKILL_CATALOG = "skill_catalog"  # skill catalog; only load_skill brings the full text in
INJECTED = "injected"  # extra system text injected by a UserPromptSubmit hook
PLAN = "plan"  # the current TODO plan
RUN_STATE = "run_state"  # round, tool-call, denial and compaction counters

# Fixed tail header so the model and log readers can tell this apart from user input.
TAIL_HEADER = "[上下文] 以下是本次请求附带的运行时上下文，不是用户输入。"

@dataclass(frozen=True)
class Block:
    """One piece of context, tagged by kind and placed by section."""

    kind: str
    content: str
    section: str

@dataclass(frozen=True)
class ContextBudget:
    """Compaction thresholds; the defaults reference the constants in policy.compaction."""

    tool_result_chars: int = compact.TOOL_RESULT_CHAR_BUDGET
    tool_result_keep_recent: int = compact.TOOL_RESULT_KEEP_RECENT
    max_messages: int = compact.MAX_MESSAGES
    keep_head: int = compact.SNIP_KEEP_HEAD
    keep_tail: int = compact.SNIP_KEEP_TAIL
    context_chars: int = compact.CONTEXT_CHAR_LIMIT
    micro_keep_recent: int = compact.MICRO_COMPACT_KEEP_RECENT
    micro_target_ratio: float = compact.MICRO_COMPACT_TARGET_RATIO
    reactive_keep_recent: int = compact.REACTIVE_KEEP_RECENT
    # Whether the character thresholds follow the real window; turn it off when comparing
    # injected values.
    from_window: bool = True
    # Fraction of the window used as the derived trigger line.
    window_ratio: float = compact.WINDOW_TRIGGER_RATIO

def effective_budget(
    limits: ContextBudget, state: RunState
) -> tuple[ContextBudget, str | None]:
    """Return the thresholds this run uses and their origin, or None when plain defaults apply."""
    if not limits.from_window:
        return limits, None

    # The reading and the part counts come from the same request, so they always arrive as a pair.
    parts = state.prompt_parts
    usage = state.last_usage
    # Chars per token is measured from the last real request, because a fixed ratio is wrong
    # for mixed scripts.
    derived = compact.derived_context_chars(
        window=state.context_window,
        prompt_tokens=None if usage is None else usage.prompt_tokens,
        chars=parts,
        ratio=limits.window_ratio,
    )
    # A missing reading or part count falls back verbatim to the constant, so the default
    # path stays unchanged.
    if derived is None or parts is None:
        return limits, None

    limit, per_token = derived
    note = (
        f"阈值随窗口派生：{limit} 字符"
        f"（窗口 {state.context_window} × {limits.window_ratio:g} × "
        f"实测 {per_token:.2f} 字符/token − 系统与工具 {parts[0] + parts[1]} 字符）"
    )
    return replace(limits, context_chars=limit), note

def announce(report: CompactReport | None, state: RunState) -> None:
    """Record a compaction in one place: one log line, one ledger entry and one event."""
    if report is None:
        return
    logger.info("compact: %s", report.describe())
    # How much a compaction saved is answered by the next model call, not estimated here.
    state.mark_compacted(report.step)
    state.emit(
        CONTEXT_COMPACTED,
        step=report.step,
        detail=report.detail,
        before=report.before,
        after=report.after,
    )

@dataclass(frozen=True)
class ComposedRequest:
    """One request's final shape: the system prompt, messages and per-block parts."""

    system: str
    messages: list[dict[str, Any]]
    parts: dict[str, int]
    reports: list[CompactReport]

    @property
    def changed(self) -> bool:
        """Whether this composition performed any compaction."""
        return bool(self.reports)

    @property
    def system_chars(self) -> int:
        return len(self.system)

    @property
    def messages_chars(self) -> int:
        """Characters of history plus the tail, under the key the loop reports to the ledger."""
        return self.parts.get("messages", 0)

class ContextManager:
    """Per-run context assembly and budget, constructed once and called every round."""

    def __init__(
        self,
        *,
        transcript: Transcript,
        state: RunState,
        config: Config,
        instructions: str | None = None,
        tool_names: Iterable[str] = (),
        budget: ContextBudget | None = None,
        summarize: Any = None,
    ) -> None:
        self.transcript = transcript
        self.state = state
        self.config = config
        # The caller's instruction override, as benchmarks and subagents each carry one;
        # None falls back to the default text from policy.prompt.
        self.instructions = (
            instructions if instructions is not None else prompt.DEFAULT_INSTRUCTIONS
        )
        self.tool_names = list(tool_names)
        self.budget = budget or ContextBudget()
        self.summarize = summarize
        self._sources: dict[str, Callable[[], Block | None]] = {}
        # The system prompt is frozen on the first compose so the provider prefix cache stays valid.
        self._system: str | None = None
        self._system_parts: dict[str, int] = {}
        self._register_defaults()

    def register_source(self, kind: str, source: Callable[[], Block | None]) -> None:
        """Register or replace a block source; a SYSTEM block only binds on the first compose."""
        self._sources[kind] = source

    def _register_defaults(self) -> None:
        self.register_source(
            INSTRUCTIONS, lambda: Block(INSTRUCTIONS, self.instructions, SYSTEM)
        )
        self.register_source(
            ENVIRONMENT, lambda: Block(ENVIRONMENT, self._render_environment(), SYSTEM)
        )
        self.register_source(BOOTSTRAP, self._bootstrap_block)
        self.register_source(SKILL_ALWAYS, self._always_skills_block)
        self.register_source(
            SKILL_CATALOG, lambda: Block(SKILL_CATALOG, self._render_skills(), SYSTEM)
        )
        self.register_source(PLAN, self._plan_block)
        self.register_source(RUN_STATE, self._run_state_block)

    def _render_environment(self) -> str:
        # Deferred import: the tools package pulls in policy.skills, so a top-level
        # import would be circular.
        from .tools import workspace

        # The run's workspace root wins here, so one process can serve several workspaces.
        root = self.state.workspace_root or workspace.WORKSPACE_ROOT
        names = "、".join(self.tool_names) if self.tool_names else "（无）"
        runtime = (
            f"{platform.system()} {platform.machine()} / Python {platform.python_version()}"
        )
        return (
            "## 环境\n"
            f"工作目录：{root}\n"
            f"运行时：{runtime}；今天：{date.today().isoformat()}\n"
            f"可用工具：{names}\n"
            "\n"
            "Act, don't explain."
        )

    def _render_skills(self) -> str:
        catalog = self.state.skills.catalog()
        return (
            "## 可用技能\n"
            f"{catalog or '（当前没有可用技能）'}\n\n"
            "Use load_skill to read the full instructions when a skill applies."
        )

    def _bootstrap_block(self) -> Block | None:
        # Read once per run: SYSTEM sources are collected only on the first compose,
        # which is also what keeps the frozen prefix byte-stable afterwards.
        root = self.state.workspace_root
        if not root:
            return None
        try:
            text = (Path(root) / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if not text.strip():
            return None
        if len(text) > prompt.AGENTS_MD_MAX_CHARS:
            text = text[: prompt.AGENTS_MD_MAX_CHARS] + prompt.TRUNCATION_NOTE
        return Block(BOOTSTRAP, f"## 工作区约定\n{text}", SYSTEM)

    def _always_skills_block(self) -> Block | None:
        # Capping lives here, not in SkillLoader: how much may stay resident is a
        # prompt-budget decision. The total cap skips by name order instead of
        # aborting, so one oversized skill does not evict the smaller ones.
        total = 0
        sections: list[str] = []
        for name, body in self.state.skills.always_bodies():
            if len(body) > prompt.SKILL_ALWAYS_MAX_CHARS:
                body = body[: prompt.SKILL_ALWAYS_MAX_CHARS] + prompt.TRUNCATION_NOTE
            if total + len(body) > prompt.SKILL_ALWAYS_TOTAL_MAX_CHARS:
                logger.warning("常驻技能总量超上限，跳过：%s", name)
                continue
            total += len(body)
            sections.append(f"### {name}\n{body}")
        if not sections:
            return None
        return Block(
            SKILL_ALWAYS,
            "## 常驻技能\n以下技能已全文载入，无需再调用 load_skill：\n\n"
            + "\n\n".join(sections),
            SYSTEM,
        )

    def _plan_block(self) -> Block | None:
        # An empty plan renders a placeholder, so no plan at all means no block.
        if not self.state.todo.items:
            return None
        return Block(PLAN, f"## 当前计划\n{self.state.todo.render()}", TAIL)

    def _run_state_block(self) -> Block:
        state = self.state
        return Block(
            RUN_STATE,
            "## 运行状态\n"
            f"第 {state.round} 轮；已执行 {state.tool_calls} 次工具调用；"
            f"被拒 {state.denials} 次（当前连击 {state.denial_streak}）；"
            f"压缩 {state.compactions} 次。",
            TAIL,
        )

    def _collect(self, section: str) -> list[Block]:
        # Sources are keyed by kind, so re-registering a kind replaces its previous source.
        blocks: list[Block] = []
        for source in self._sources.values():
            block = source()
            if block is not None and block.section == section and block.content:
                blocks.append(block)
        return blocks

    def compose(self, *, injected: list[str] | None = None) -> ComposedRequest:
        """Compact and render; injected text joins the system prompt on the first round only."""
        reports = self._compact()
        if self._system is None:
            # Injected background that later disappears is worse than never injecting it at all.
            self._freeze_system(list(injected or []))
        return self._assemble(reports)

    def system_prompt(self) -> str:
        """Render the system prompt alone, without compaction or tail, for baseline arms."""
        if self._system is None:
            self._freeze_system([])
        assert self._system is not None  # _freeze_system always assigns
        return self._system

    def _freeze_system(self, injected: list[str]) -> None:
        # Per-kind character counts are frozen with the text so accounting matches the prompt.
        blocks = self._collect(SYSTEM)
        texts = [block.content for block in blocks]
        texts += [str(text) for text in injected if str(text).strip()]
        self._system = "\n\n".join(texts)
        self._system_parts = {}
        for block in blocks:
            self._system_parts[block.kind] = (
                self._system_parts.get(block.kind, 0) + len(block.content)
            )
        for text in injected:
            self._system_parts[INJECTED] = (
                self._system_parts.get(INJECTED, 0) + len(str(text))
            )

    def render(self) -> ComposedRequest:
        """Re-render after a fallback compaction; the system prompt itself stays fixed."""
        if self._system is None:
            raise RuntimeError("render() 之前必须先 compose()")
        return self._assemble([])

    def _assemble(self, reports: list[CompactReport]) -> ComposedRequest:
        # The tail becomes one extra user message and is never written to the transcript.
        tail_blocks = self._collect(TAIL)
        messages = self.transcript.as_messages()
        tail_chars = 0
        if tail_blocks:
            content = TAIL_HEADER + "\n\n" + "\n\n".join(
                block.content for block in tail_blocks
            )
            message = {"role": "user", "content": content}
            messages.append(message)
            tail_chars = message_chars(message)

        parts = dict(self._system_parts)
        for block in tail_blocks:
            parts[block.kind] = parts.get(block.kind, 0) + len(block.content)
        parts["history"] = self.transcript.estimate_chars()
        parts["messages"] = parts["history"] + tail_chars
        return ComposedRequest(
            system=self._system or "",
            messages=messages,
            parts=parts,
            reports=reports,
        )

    def _compact(self) -> list[CompactReport]:
        """Run the pipeline cheapest first; the fourth step happens at most once per run."""
        limits, note = effective_budget(self.budget, self.state)
        if note is not None:
            logger.debug("compact: %s", note)
        reports: list[CompactReport] = []
        # Spill files follow the workspace root, because one process can serve several workspaces.
        workdir = Path(self.state.workspace_root) if self.state.workspace_root else None

        def run(report: CompactReport | None) -> None:
            if report is None:
                return
            if note is not None:
                # A derived threshold records its origin, which the UI reads to explain it.
                report = replace(report, detail=f"{report.detail}（{note}）")
            reports.append(report)
            announce(report, self.state)

        # Steps one and two need no API call, so they run every round.
        run(
            compact.tool_result_budget(
                self.transcript,
                budget=limits.tool_result_chars,
                keep_recent=limits.tool_result_keep_recent,
                workdir=workdir,
                tag=self.state.run_tag,
            )
        )
        run(
            compact.snip_compact(
                self.transcript,
                max_messages=limits.max_messages,
                keep_head=limits.keep_head,
                keep_tail=limits.keep_tail,
            )
        )

        # Step three is free, so it comes before the paid one.
        if self.transcript.estimate_chars() > limits.context_chars:
            run(
                compact.micro_compact(
                    self.transcript,
                    limit=limits.context_chars,
                    keep_recent=limits.micro_keep_recent,
                    target_ratio=limits.micro_target_ratio,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
            )

        # Step four pays for a summarization call, so state allows it at most once per run.
        if self.transcript.estimate_chars() > limits.context_chars:
            if self.state.compacted:
                logger.info("compact: 自动压缩本运行已用过一次，跳过")
            else:
                report = compact.compact_history(
                    self.transcript,
                    config=self.config,
                    chat=self.summarize,
                    limit=limits.context_chars,
                    workdir=workdir,
                    tag=self.state.run_tag,
                )
                if report is not None:
                    # Only this branch sets the flag, which is what bounds step four to one run.
                    self.state.compacted = True
                run(report)

        return reports

    def reactive(self) -> CompactReport | None:
        """Fallback after a provider overflow: summarize older history and keep a recent tail."""
        report = compact.reactive_compact(
            self.transcript,
            config=self.config,
            chat=self.summarize,
            workdir=Path(self.state.workspace_root)
            if self.state.workspace_root
            else None,
            keep_recent=self.budget.reactive_keep_recent,
            tag=self.state.run_tag,
        )
        # The caller bounds this to one attempt per run through state.retried.
        announce(report, self.state)
        return report


# ──────────────────────────── loop ────────────────────────────

# Agent execution loop: it decides only the order of model calls, tool batches and termination.

# Only annotations use it: annotations are lazy, so the policy layer stays out of run time.


# Extra rounds granted after a blocked Stop; stops a faulty callback from looping forever.
MAX_STOP_BLOCKS = 1

# Follow-up prompt for a round with no visible text; it shares the Stop-block budget above.
BLANK_ANSWER_NUDGE = (
    "上一轮没有可见正文（{reason}）。请直接给出可见答复：总结已完成的事与当前结论；"
    "要继续动手就发起工具调用。"
)
# Closing text when even the follow-up produced no text; it must stay visible to the user.
BLANK_ANSWER_NOTICE = (
    "（本次运行没有产生可见答复：{reason}。请看上一条工具结果，或重试这一轮。）"
)

class RunCancelled(RuntimeError):
    """Cancellation, raised only at step boundaries so no compensating write is needed."""

def _submit_input(
    transcript: Transcript, state: RunState, tool_names: list[str]
) -> tuple[int, list[str]] | None:
    """Run the submit hook; returns the trigger message index and injected context, or None."""
    index = transcript.last_user_index()
    if index is None:
        return None

    submit: dict[str, Any] = {
        "prompt": transcript.text_at(index),
        "messages": transcript.as_messages(),
        "injected": [],
        # The injected environment information must match the workspace actually resolved.
        "workspace_root": state.workspace_root,
        "permission_mode": state.permission_mode,
        "tool_names": list(tool_names),
    }
    if state.hooks.trigger("UserPromptSubmit", submit) == BLOCK:
        logger.warning("UserPromptSubmit 被拦截，未调用模型")
        return None

    return index, [str(item) for item in (submit.get("injected") or [])]

def _blank_answer(turn: Turn) -> bool:
    """True when the round has no visible text; truncation and an empty stop look alike here."""
    return not turn.text.strip()

def _blank_reason(turn: Turn) -> str:
    """Explain the missing text with concrete numbers rather than an 'unknown' placeholder."""
    thinking = turn.reasoning.strip()
    if thinking:
        base = f"最近一轮只产出了思考（{len(thinking)} 字符思维链）"
    elif turn.finish_reason == "length":
        base = "最近一轮在输出上限处被截断"
    else:
        base = "最近一轮输出为空"
    tokens = turn.usage.reasoning_tokens
    return f"{base}，推理 token {tokens}" if tokens else base

def agent_loop(
    messages: list[dict[str, Any]],
    *,
    system: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    registry: dict[str, ToolImpl] | None = None,
    config: Config | None = None,
    chat: Callable[..., Turn] = chat_completion,
    # Summary calls (compaction, fallback) get their own entry point so that a streaming
    # main round never mixes summary text into the delta stream.
    summarize: Callable[..., Turn] | None = None,
    # Injecting the thresholds keeps a measurement run free of unrelated code changes.
    budget: ContextBudget | None = None,
    auto_approve: bool = False,
    # A shared ledger lets one approval by the caller cover a whole run, subagents included.
    permission_mode: str | None = None,
    ledger: "ApprovalLedger | None" = None,
    security: "RunSecurity | None" = None,
    workspace_root: str | None = None,
    max_tokens: int | None = DEFAULT_MAX_TOKENS,
    max_stop_blocks: int = MAX_STOP_BLOCKS,
    # Denials counted without a single pass in between; reaching it ends the run.
    max_consecutive_denials: int = MAX_CONSECUTIVE_DENIALS,
    # None uses config.max_parallel_tool_calls; 1 is fully serial; only concurrency-safe
    # tools share a segment, everything else is a barrier.
    max_parallel_tools: int | None = None,
    on_message: Callable[[dict[str, Any]], Any] | None = None,
    ask: AskUser | None = None,
    on_event: RunObserver | None = None,
    state: RunState | None = None,
    hooks: HookRegistry | None = None,
) -> str:
    """Cycle model calls and tool batches until the model stops asking; returns the final text."""
    config = config or resolve_chat()
    if config.context_window is None:
        # Ask the provider once per process when neither the environment nor the built-in
        # table knows the window; every run path passes through here, so no caller repeats it.
        probed = fetch_context_length(config)
        if probed:
            config = replace(config, context_window=probed)
    summarize = summarize or chat
    tools = TOOLS if tools is None else tools
    registry = TOOL_IMPLS if registry is None else registry
    # Single place where the concurrency limit is resolved: argument first, config second.
    parallel_limit = (
        config.max_parallel_tool_calls
        if max_parallel_tools is None
        else max_parallel_tools
    )

    # Sole message channel: every message this run creates or rewrites is reported once, in order.
    def emit(message: dict[str, Any]) -> None:
        if on_message is not None:
            on_message(message)

    # Registry and system prompt belong to state, and a supplied state is the sole authority,
    # so the convenience arguments above are ignored in that case.
    state = state or RunState.for_run(
        auto_approve=auto_approve,
        ask=ask,
        observer=on_event,
        permission_mode=permission_mode,
        ledger=ledger,
        security=security,
        workspace_root=workspace_root,
        hooks=hooks,
        # The window is part of the model config, so the utilization denominator follows it.
        context_window=config.context_window,
    )
    # Callers that build state first see a None window, and the probe happens only here, so
    # without this backfill the web view would never show utilization.
    if state.context_window is None:
        state.context_window = config.context_window

    # Context assembly, compaction and the tail blocks belong to the manager; the loop hands
    # over only this run's facts: instruction override, real tool list, budget, summarizer.
    ctx = ContextManager(
        transcript=Transcript(messages),
        state=state,
        config=config,
        instructions=system,
        tool_names=[str(item["function"]["name"]) for item in tools],
        budget=budget,
        summarize=summarize,
    )
    transcript = ctx.transcript

    trigger = _submit_input(transcript, state, [str(item["function"]["name"]) for item in tools])
    if trigger is None:
        return ""
    index, injected = trigger
    emit(transcript.as_messages()[index])

    def note_prompt_parts(request: ComposedRequest) -> None:
        """Record the character counts of the three blocks about to be sent."""
        # System prompt and tool definitions never reach the client, so the split can only be
        # measured here; usage_report() turns the character shares into token estimates.
        # Tail blocks count as messages because they are sent, even though they are not stored.
        state.record_prompt_parts(
            system=request.system_chars,
            tools=len(json.dumps(tools, ensure_ascii=False)) if tools else 0,
            messages=request.messages_chars,
        )

    # No round cap by design, since the exits are the model's answer and the two cancel
    # checkpoints; this file must stay one scheduler, not a handwritten state machine.
    for round_index in itertools.count(1):
        state.round = round_index
        state.check_cancelled()  # Cancel checkpoint 1: before the round starts.

        state.emit(RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

        # Compaction steps run inside compose: the cheap ones every round, the costly ones
        # only past budget, and the last of them at most once per run.
        request = ctx.compose(injected=injected)

        # Model call; an over-context error triggers one fallback compaction and one retry.
        note_prompt_parts(request)
        try:
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            ctx.reactive()
            # Reactive compaction changed the candidates, so tail and parts are recomputed.
            request = ctx.render()
            note_prompt_parts(request)
            turn = chat(
                config,
                request.messages,
                system=request.system,
                tools=tools,
                max_tokens=max_tokens,
            )

        state.record_usage(turn.usage)
        transcript.append(turn.message)
        emit(turn.message)
        # The snapshot follows this round's real reading; as a transient event it stays out
        # of the replay budget, so a refresh falls back to the value persisted in the session.
        state.emit(
            RUN_STATUS,
            round=round_index,
            tokens=state.tokens,
            activity="model",
            finish_reason=turn.finish_reason,
            usage=state.usage_report(),
        )
        logger.info(
            "round=%d finish=%s tool_calls=%d tokens=%d",
            round_index,
            turn.finish_reason or "-",
            len(turn.tool_calls),
            turn.usage.total_tokens,
        )

        if not turn.tool_calls:
            # Stop path: a callback may ask to hold the exit open.
            stop: dict[str, Any] = {
                "final_text": turn.text,
                "messages": transcript.as_messages(),
                "summary": None,
                "nudge": None,
                **state.snapshot(),
            }
            blocked = state.hooks.trigger("Stop", stop) == BLOCK
            blank = _blank_answer(turn)
            reason = _blank_reason(turn) if blank else ""
            if blank and not blocked:
                # A round without visible text is not an answer: treat it as a blocked Stop
                # and ask again, letting a callback's own nudge win when it set one.
                blocked = True
                stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
            if blocked and state.stop_blocks < max_stop_blocks:
                state.stop_blocks += 1
                nudge = stop.get("nudge")
                if nudge:
                    message = {"role": "user", "content": str(nudge)}
                    transcript.append(message)
                    state.emit(STOP_NUDGE, content=str(nudge), message=message)
                    emit(message)
                logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
                continue
            if blank:
                # Even the follow-up produced nothing: close with visible text rather than
                # returning an empty string, which is the "success with no answer" path.
                notice = BLANK_ANSWER_NOTICE.format(reason=reason)
                message = {"role": "assistant", "content": notice}
                transcript.append(message)
                emit(message)
                logger.warning(
                    "仍然没有可见正文（%s；finish_reason=%s，round=%d），以 notice 收尾",
                    reason,
                    turn.finish_reason or "-",
                    round_index,
                )
                return notice
            if blocked:
                logger.warning("Stop 拦截次数已达上限 %d，照常退出", max_stop_blocks)
            return turn.text

        state.check_cancelled()  # Cancel checkpoint 2: before each tool batch.
        outcomes = execute_batch(
            turn.tool_calls,
            state=state,
            registry=registry,
            round_index=round_index,
            # Argument validation reuses the very schema that was sent to the model.
            schemas={
                str(item["function"]["name"]): item["function"]["parameters"]
                for item in tools
            },
            # In-batch concurrency: safe tools share a segment, exclusive calls are barriers.
            max_parallel=parallel_limit,
        )
        for outcome in outcomes:
            message = {
                "role": "tool",
                "tool_call_id": outcome.tool_call_id,
                "content": outcome.content,
            }
            transcript.append(message)
            emit(message)

        if state.denial_streak >= max_consecutive_denials:
            # Denied again and again without a single pass means the same wall is being hit;
            # the halt text travels as a normal message, so no new event type is needed.
            halt = (
                f"（运行已停止：连续 {state.denial_streak} 次工具调用被权限策略拒绝，"
                "期间没有一次通过。请向用户说明需要哪个目标或哪条命令的授权，"
                "再开新一轮。）"
            )
            logger.warning(
                "连续 %d 次工具调用被拒，运行提前结束", state.denial_streak
            )
            message = {"role": "assistant", "content": halt}
            transcript.append(message)
            emit(message)
            return halt
    # Static checkers do not accept an endless loop, so the exit has to be spelled out even
    # though run time never reaches it: the loop leaves through return, cancel or an error.
    raise AssertionError("轮次循环没有正常出口")



# ──────────────────────────── spec ────────────────────────────

@dataclass(frozen=True)
class RunSpec:
    """一次运行的只读输入：取代旧 agent_loop 的 22 参数散布。

    resolve() 是唯一构造入口——模型配置解析、窗口探测、工具表与 schemas、
    并发上限都在这里收口；运行期的可变状态（RunState）不属于它。
    """

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
    ledger: ApprovalLedger | None
    security: RunSecurity | None
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
        ledger: ApprovalLedger | None = None,
        security: RunSecurity | None = None,
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


# ──────────────────────────── run ────────────────────────────

class Run:
    """一次运行：prepare → 轮次循环（compose → chat → tools）→ 终止，单出口。

    与 agent_loop 的分工：这里只换表达方式（显式阶段方法 + 单一出口），
    行为逐字节一致由 tests/test_run.py 的序列一致性用例钉住；阶段 37 起
    行为演进先改这里，旧 loop 分区在阶段 40 删除。
    """

    def __init__(
        self,
        messages: list[dict[str, Any]],
        spec: RunSpec,
        *,
        state: RunState | None = None,
        on_message: Callable[[dict[str, Any]], Any] | None = None,
        ask: AskUser | None = None,
        on_event: RunObserver | None = None,
    ) -> None:
        self.messages = messages
        self.spec = spec
        self.state = state
        self.on_message = on_message
        self.ask = ask
        self.on_event = on_event

    def run(self) -> str:
        spec = self.spec
        state = self.state
        if state is None:
            state = RunState.for_run(
                auto_approve=spec.auto_approve,
                ask=self.ask,
                observer=self.on_event,
                permission_mode=spec.permission_mode,
                ledger=spec.ledger,
                security=spec.security,
                workspace_root=spec.workspace_root,
                hooks=spec.hooks,
                context_window=spec.config.context_window,
            )
        # 调用方自建 state 时窗口还没探测（探测只发生在 resolve），这里回填。
        if state.context_window is None:
            state.context_window = spec.config.context_window

        ctx = ContextManager(
            transcript=Transcript(self.messages),
            state=state,
            config=spec.config,
            instructions=spec.instructions,
            tool_names=spec.tool_names,
            budget=spec.budget,
            summarize=spec.summarize or spec.chat,
        )
        transcript = ctx.transcript

        trigger = _submit_input(transcript, state, spec.tool_names)
        if trigger is None:
            return ""
        index, injected = trigger
        self._emit(transcript.as_messages()[index])

        for round_index in itertools.count(1):
            state.round = round_index
            state.check_cancelled()  # 取消检查点 1：轮次开始前
            state.emit(RUN_STATUS, round=round_index, tokens=state.tokens, activity="model")

            request = ctx.compose(injected=injected)
            self._note_prompt_parts(state, request)
            turn = self._call_model(state, ctx, request)

            state.record_usage(turn.usage)
            transcript.append(turn.message)
            self._emit(turn.message)
            state.emit(
                RUN_STATUS,
                round=round_index,
                tokens=state.tokens,
                activity="model",
                finish_reason=turn.finish_reason,
                usage=state.usage_report(),
            )
            logger.info(
                "round=%d finish=%s tool_calls=%d tokens=%d",
                round_index,
                turn.finish_reason or "-",
                len(turn.tool_calls),
                turn.usage.total_tokens,
            )

            if not turn.tool_calls:
                final = self._finish(state, transcript, turn)
                if final is not None:
                    return final
                continue

            state.check_cancelled()  # 取消检查点 2：工具批前
            outcomes = execute_batch(
                turn.tool_calls,
                state=state,
                registry=spec.registry,
                round_index=round_index,
                schemas=spec.schemas,
                max_parallel=spec.parallel_limit,
            )
            for outcome in outcomes:
                message = {
                    "role": "tool",
                    "tool_call_id": outcome.tool_call_id,
                    "content": outcome.content,
                }
                transcript.append(message)
                self._emit(message)

            if state.denial_streak >= spec.max_consecutive_denials:
                halt = (
                    f"（运行已停止：连续 {state.denial_streak} 次工具调用被权限策略拒绝，"
                    "期间没有一次通过。请向用户说明需要哪个目标或哪条命令的授权，"
                    "再开新一轮。）"
                )
                logger.warning("连续 %d 次工具调用被拒，运行提前结束", state.denial_streak)
                message = {"role": "assistant", "content": halt}
                transcript.append(message)
                self._emit(message)
                return halt
        raise AssertionError("轮次循环没有正常出口")  # pragma: no cover

    def _call_model(self, state: RunState, ctx: ContextManager, request: ComposedRequest) -> Turn:
        spec = self.spec
        try:
            return spec.chat(
                spec.config,
                request.messages,
                system=request.system,
                tools=spec.tools,
                max_tokens=spec.max_tokens,
            )
        except PromptTooLongError:
            if state.retried:
                raise
            state.retried = True
            logger.warning("compact: 模型报上下文超限，兜底压缩后重试一次")
            ctx.reactive()
            request = ctx.render()
            self._note_prompt_parts(state, request)
            return spec.chat(
                spec.config,
                request.messages,
                system=request.system,
                tools=spec.tools,
                max_tokens=spec.max_tokens,
            )

    def _finish(self, state: RunState, transcript: Transcript, turn: Turn) -> str | None:
        """终止路径（阶段 37 独立成节）：返回最终文本；None = nudge 已注入，续轮。"""
        spec = self.spec
        stop: dict[str, Any] = {
            "final_text": turn.text,
            "messages": transcript.as_messages(),
            "summary": None,
            "nudge": None,
            **state.snapshot(),
        }
        blocked = state.hooks.trigger("Stop", stop) == BLOCK
        blank = _blank_answer(turn)
        reason = _blank_reason(turn) if blank else ""
        if blank and not blocked:
            # 没有可见正文的一轮不算答复：按一次 Stop 拦截处理并补问
            blocked = True
            stop["nudge"] = BLANK_ANSWER_NUDGE.format(reason=reason)
        if blocked and state.stop_blocks < spec.max_stop_blocks:
            state.stop_blocks += 1
            nudge = stop.get("nudge")
            if nudge:
                message = {"role": "user", "content": str(nudge)}
                transcript.append(message)
                state.emit(STOP_NUDGE, content=str(nudge), message=message)
                self._emit(message)
            logger.info("Stop 被拦截（第 %d 次），继续循环", state.stop_blocks)
            return None
        if blank:
            notice = BLANK_ANSWER_NOTICE.format(reason=reason)
            message = {"role": "assistant", "content": notice}
            transcript.append(message)
            self._emit(message)
            logger.warning(
                "仍然没有可见正文（%s；finish_reason=%s），以 notice 收尾",
                reason,
                turn.finish_reason or "-",
            )
            return notice
        if blocked:
            logger.warning("Stop 拦截次数已达上限 %d，照常退出", spec.max_stop_blocks)
        return turn.text

    def _emit(self, message: dict[str, Any]) -> None:
        if self.on_message is not None:
            self.on_message(message)

    def _note_prompt_parts(self, state: RunState, request: ComposedRequest) -> None:
        state.record_prompt_parts(
            system=request.system_chars,
            tools=len(json.dumps(self.spec.tools, ensure_ascii=False))
            if self.spec.tools
            else 0,
            messages=request.messages_chars,
        )
