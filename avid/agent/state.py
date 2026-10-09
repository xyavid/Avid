"""Mutable state of one run: counters, one-shot flags, the usage ledger and run objects."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ..providers.usage import Usage, hit_ratio
from ..security.permission import (
    PERMISSION_FULL,
    PERMISSION_NORMAL,
    ApprovalLedger,
    RunSecurity,
    build_run_security,
)
from . import hooks as hooks_module
from .events import RunObserver, event
from .skills import SkillLoader, default_skills_dir
from .todo import TodoList

if TYPE_CHECKING:  # Annotations only: these two are never imported at run time.
    from ..security.permission import AskQuestion, AskUser
    from .tools.mcp import McpManager

# Abort the run after this many consecutive denials; any allowed call resets the streak.
MAX_CONSECUTIVE_DENIALS = 5


class Checkpointer(Protocol):
    """files 工具写路径的写前快照契约；实现在 agent/checkpoints.py。

    None=快照成功（或无需快照）；字符串=备份失败的错误文案，调用方必须拒绝写入，
    不留无快照的改动。
    """

    def snapshot(self, path: Path) -> str | None: ...


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

    # 记录口径两值：normal（默认直接跑）/ full（完全访问）。架构已无模式阶梯。
    permission_mode: str = PERMISSION_NORMAL

    # Run security spec (sandbox, audit), the one place tools read it from;
    # built in __post_init__ when the constructor was not given one.
    security: RunSecurity | None = None

    # "Approved once" ledger, one per run and in memory only; a subagent shares the parent's.
    ledger: ApprovalLedger = field(default_factory=ApprovalLedger)

    # Workspace root for this run; None defers to tools.workspace.WORKSPACE_ROOT, read at call time.
    workspace_root: str | None = None

    # 临时对话（阶段 54）：这个运行来自一个临时会话。三个消费方——工具表摘掉写入工具、
    # 沙箱把工作区挂只读、环境块里写一句「这是临时对话」；子运行逐字段继承同一个事实。
    scratch: bool = False

    # 本次运行的模型覆盖与推理强度（界面选的那两个，阶段 55）：子运行要用同一份——
    # 不然用户选了 A 模型，子 agent 却按设置里的绑定 B 跑（花的还是钱）。
    # None = 没有覆盖，按设置解析（与父运行同一条路）。
    model_ref: str | None = None
    effort: str | None = None

    # Approval callback injection point; None falls back to the default stdin-based prompter.
    ask: AskUser | None = None
    #: 模型主动提问的通道（阶段 58）：(question, options) -> 答案；None = 没有通道或没人答。
    #: 与 ``ask``（毁灭级确认的 bool 通道）分开：那条路是安全裁决，这条是普通问答，
    #: 混在一起会让「允许/拒绝」的语义漏进工具层。
    question: AskQuestion | None = None

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
    # Write-ahead file checkpointer, wired by session-aware entry points; None (sessionless
    # runs, tests) means the file tools write without snapshotting.
    checkpoint: Checkpointer | None = None
    # Hook registry owned by the run, reached through the module so replacing the default works.
    hooks: "hooks_module.HookRegistry" = field(
        default_factory=lambda: hooks_module.DEFAULT_HOOKS
    )

    def __post_init__(self) -> None:
        """Resolve the security spec once here, because tools read it from worker threads."""
        if self.security is None:
            self.security = build_run_security(
                full=self.permission_mode == PERMISSION_FULL,
                root=self.workspace_root,
                run_tag=self.run_tag,
                # 临时对话直接构造 RunState 时也只有读沙箱——这条不能只挂在运行装配上
                read_only=self.scratch,
            )

    @classmethod
    def for_run(
        cls,
        *,
        auto_approve: bool = False,
        ask: AskUser | None = None,
        question: AskQuestion | None = None,
        observer: RunObserver | None = None,
        permission_mode: str | None = None,
        ledger: ApprovalLedger | None = None,
        workspace_root: str | None = None,
        hooks: "hooks_module.HookRegistry | None" = None,
        context_window: int | None = None,
        full: bool = False,
        security: RunSecurity | None = None,
        home: str | None = None,
        audit_dir: str | None = None,
        audit_enabled: bool = True,
        scratch: bool = False,
        model_ref: str | None = None,
        effort: str | None = None,
    ) -> "RunState":
        """Build a run state and rescan skills; full 只由显式授权（full=True）产生。

        A caller-supplied security spec is authoritative and reused as-is, so the enforcement
        and the start event cannot disagree.
        """
        is_full = full or permission_mode == PERMISSION_FULL
        return cls(
            auto_approve=auto_approve,
            ask=ask,
            question=question,
            observer=observer,
            permission_mode=PERMISSION_FULL if is_full else PERMISSION_NORMAL,
            security=security
            if security is not None
            else build_run_security(
                full=is_full,
                root=workspace_root,
                home=home,
                # 临时对话（scratch）自带只读沙箱：这里与运行装配那条路必须是同一个事实，
                # 否则直接构造 RunState 的调用方（CLI、测试）会拿到可写沙箱。
                read_only=scratch,
                audit_dir=audit_dir,
                audit_enabled=audit_enabled,
            ),
            ledger=ledger if ledger is not None else ApprovalLedger(),
            workspace_root=workspace_root,
            scratch=scratch,
            model_ref=model_ref,
            effort=effort,
            context_window=context_window,
            # The skill directory follows the workspace root and falls back to cwd.
            skills=SkillLoader(default_skills_dir(workspace_root)).scan(),
            hooks=hooks if hooks is not None else hooks_module.DEFAULT_HOOKS,
        )

    def sandbox_grants(self) -> tuple[tuple[str, str], ...]:
        """Outside paths already granted for this run, as (path, ro/rw) pairs for the sandbox."""
        return self.ledger.path_grants()

    def security_summary(self) -> dict[str, Any]:
        """Security snapshot（permission + 沙箱）carried into events and REST responses."""
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
            from .run import RunCancelled

            raise RunCancelled(self.cancel_reason or "cancelled")
        if self.cancel_probe is not None:
            reason = self.cancel_probe()
            if reason:
                from .run import RunCancelled

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
