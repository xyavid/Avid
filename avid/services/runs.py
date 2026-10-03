"""In-process run registry: wraps the agent loop into an observable, replayable run."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from ..agent import commands as commands_module
from ..agent.events import (
    ASSISTANT_DELTA,
    ASSISTANT_MESSAGE,
    DELTA_EVENT_TYPES,
    DURABLE_SET,
    REASONING_DELTA,
    RESYNC,
    RUN_CANCELLED,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    RUN_STATUS,
    STOP_NUDGE,
    STREAM_HEARTBEAT_SECONDS,
    TERMINAL_EVENT_TYPES,
    TOOL_RESULT_MESSAGE,
    USER_MESSAGE,
    RunEvent,
    now_ms,
)
from ..agent.run import Run, RunCancelled
from ..agent.spec import RunSpec
from ..agent.state import RunState
from ..agent.tools import TOOLS, build_toolset
from ..agent.tools.mcp import McpManager
from ..providers.byok import resolve_chat
from ..providers.client import LLMError, chat_completion, stream_completion
from ..providers.config import ConfigError
from ..security.permission import build_run_security, full_grant_error
from ..session import (
    DEFAULT_BRANCH,
    SessionError,
    SessionMetadata,
    SessionRecorder,
    messages_for_branch,
)
from .approvals import APPROVAL_TIMEOUT_SECONDS, ApprovalTable
from .errors import (
    InvalidRequest,
    RunBusy,
    RunFinished,
    RunNotFound,
    SessionNotFound,
    SessionReadError,
)
from .workspace_registry import SESSION_DIR
from .workspaces import WorkspaceService

logger = logging.getLogger("avid.services.runs")

# Bounded replay buffer per run; an eviction is what forces a resync (I5).
REPLAY_BUFFER_SIZE = 512

# Terminal records are kept for a while because reconnect and reconciliation look them up by
# run_id, but each record holds up to buffer_size durable events plus all the deltas of that
# period, so keeping them forever would leak memory.
TERMINAL_RETENTION_SECONDS = 600.0

# Hard cap on retained records, independent of the retention window above.
MAX_RETAINED_RUNS = 200

# Cap on buffered events (durable + transient + delta). Durable replay is budgeted separately,
# so this only absorbs a delta flood: the oldest prefix is dropped.
MAX_EVENT_BUFFER = 4096

# The count-based sweep must leave just-finished records alone: a subscriber may still be
# consuming their buffer, and a dropped buffer is exactly the silent gap I5 forbids.
SWEEP_MIN_AGE_SECONDS = 30.0

# Message role -> durable message event.
_MESSAGE_EVENTS = {
    "user": USER_MESSAGE,
    "assistant": ASSISTANT_MESSAGE,
    "tool": TOOL_RESULT_MESSAGE,
}


@dataclass
class RunRecord:
    """Live view of one run; the registry and the run thread both write its fields."""

    run_id: str
    session_id: str
    started_at: int
    status: str = "running"  # running | awaiting_approval | finished | failed | cancelled
    round: int = 0
    tokens: int = 0
    # Latest snapshot in the unified usage schema; None means no reading has arrived yet.
    usage: dict[str, Any] | None = None
    text: str = ""
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    finished_at: int | None = None
    # 本次运行的模型覆盖（界面选的那个）；None = 按设置解析。
    model: str | None = None
    # 会话内命令（"compact" / "unknown"）；None = 普通运行。
    command: str | None = None

    # Event buffer holding durable and transient events; deltas never take part in replay.
    events: list[RunEvent] = field(default_factory=list)
    dropped: int = 0  # events evicted from the head; absolute index = dropped + position
    # Absolute indices of the buffered durable events, ascending. Maintaining them keeps the
    # durable count O(1): every delta calls _trim, so a full scan would make one long reply
    # quadratic in the thread that reads the model SSE stream.
    durable_index: list[int] = field(default_factory=list)
    # Highest evicted durable seq, which answers "did the cursor fall out of the buffer" more
    # reliably than the head of the list, whose first element may be a transient event.
    evicted_upto: int = 0
    next_seq: int = 1

    condition: threading.Condition = field(default_factory=threading.Condition)
    approvals: ApprovalTable | None = None
    state: RunState | None = None
    # Sole persistence point for this run, installed by the run thread: _finish writes the
    # usage snapshot through it before announcing the terminal state.
    recorder: SessionRecorder | None = None
    # Tag for injected messages, id(message) -> kind; on_event tags them, on_message reads it.
    injected: dict[int, str] = field(default_factory=dict)
    # Async subscribers as (event loop, wake event). A bridge thread turns the condition
    # notify into call_soon_threadsafe; synchronous subscribers are not listed here.
    watchers: list[tuple[asyncio.AbstractEventLoop, asyncio.Event]] = field(
        default_factory=list
    )
    # At most one bridge thread per record, started lazily and gone once nobody is watching.
    bridge: threading.Thread | None = None

    @property
    def terminal(self) -> bool:
        return self.status in ("finished", "failed", "cancelled")

    def absolute_index(self) -> int:
        return self.dropped + len(self.events)

    def to_dict(self) -> dict[str, Any]:
        pending = self.approvals.pending() if self.approvals is not None else []
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "round": self.round,
            "tokens": self.tokens,
            "usage": self.usage,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "pending_approvals": [item.to_dict() for item in pending],
        }


class RunRegistry:
    """In-process run registry holding at most one active run per session."""

    def __init__(
        self,
        workspaces: WorkspaceService,
        *,
        chat: Callable[..., Any] | None = None,
        tool_registry: dict[str, Any] | None = None,
        buffer_size: int = REPLAY_BUFFER_SIZE,
        approval_timeout: float = APPROVAL_TIMEOUT_SECONDS,
        retention_seconds: float = TERMINAL_RETENTION_SECONDS,
        max_runs: int = MAX_RETAINED_RUNS,
        max_events: int = MAX_EVENT_BUFFER,
    ) -> None:
        self.workspaces = workspaces
        self.chat = chat
        # The tool registry is injectable so tests can stub bash; empty means the real one.
        self.tool_registry = tool_registry
        self.buffer_size = buffer_size
        self.approval_timeout = approval_timeout
        self._retention_ms = max(0, int(retention_seconds * 1000))
        self._max_runs = max(1, max_runs)
        self.max_events = max(1, max_events)
        self._runs: dict[str, RunRecord] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()
        self._sessions: dict[str, Any] = {}  # run_id -> session handle held during the run
        # One handle lock per session: the session layer allows a single handle while the read
        # path and the run path both want it, so every open/close takes this lock first, always
        # in the order handle lock then self._lock.
        self._session_locks: dict[str, threading.RLock] = {}
        # Threads waiting on or holding that handle lock. The lock is dropped only when the
        # count reaches zero and the session has no active run: two live locks would break the
        # one-handle-per-session exclusion.
        self._session_lock_users: dict[str, int] = {}

    @contextmanager
    def session_lock(self, session_id: str):
        """Serialize handle acquisition and release for one session."""
        # Open and close only while holding it: otherwise a read path and the run thread race
        # into SessionAlreadyOpenError, which shows up either as a run that fails immediately
        # or as a 500 saying the session is closed.
        with self._lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[session_id] = lock
            self._session_lock_users[session_id] = (
                self._session_lock_users.get(session_id, 0) + 1
            )
        try:
            with lock:
                yield
        finally:
            # Drop the lock once nobody waits or holds it and no run is active: a long-lived
            # server otherwise accumulates one lock per session it has ever visited.
            with self._lock:
                remaining = self._session_lock_users.get(session_id, 1) - 1
                if remaining > 0:
                    self._session_lock_users[session_id] = remaining
                else:
                    self._session_lock_users.pop(session_id, None)
                    if session_id not in self._active:
                        self._session_locks.pop(session_id, None)

    # ---- Lifecycle ----

    def start(
        self,
        session_id: str,
        prompt: str,
        *,
        auto_approve: bool = False,
        chat: Callable[..., Any] | None = None,
        branch: str = DEFAULT_BRANCH,
        permission: str | None = None,
        full_ack: bool = False,
        model: str | None = None,
    ) -> RunRecord:
        """Register a run and start its thread; raises RunBusy or SessionNotFound."""
        # The branch decides which chain the run appends to, and the permission argument
        # defaults to what the owning workspace allows; full_ack is the second gate for full.
        found = self.workspaces.find_session(session_id)
        if found is None:
            raise SessionNotFound(f"没有这个会话：{session_id}")
        workspace, metadata = found
        mode = permission or workspace.default_permission
        problem = full_grant_error(mode, acknowledged=full_ack, source="web")
        if problem is not None:
            raise InvalidRequest(problem)

        # 会话内命令（内核单点解析）：/<技能名> 把全文当作用户输入（正常运行），
        # /compact 与未知命令走 _run 的命令分支（不调模型，直接以文本收尾）。
        command: str | None = None
        if prompt.startswith("/"):
            match = commands_module.match_command(
                prompt, skill_names=commands_module.skill_names(workspace_root=workspace.root)
            )
            if match is not None and match.kind == commands_module.KIND_SKILL:
                body = commands_module.skill_text(match.name, workspace_root=workspace.root)
                if body is not None:
                    prompt = body
            elif match is not None and match.kind == commands_module.KIND_COMMAND:
                command = match.name
            elif match is not None:
                command = "unknown"

        with self.session_lock(session_id):
            with self._lock:
                if session_id in self._active:
                    raise RunBusy(f"会话已有活动 run：{self._active[session_id]}")
                run_id = f"run_{uuid.uuid4().hex[:12]}"
                record = RunRecord(
                    run_id=run_id,
                    session_id=session_id,
                    started_at=now_ms(),
                    model=(model or "").strip() or None,
                    command=command,
                )

                def emit_approval(type: str, **data: Any) -> None:
                    self.emit(record, type, **data)

                record.approvals = ApprovalTable(
                    emit=emit_approval,
                    set_status=lambda status: self._set_status(record, status),
                    is_cancelled=lambda: record.cancel_requested,
                    timeout=self.approval_timeout,
                )
                self._runs[run_id] = record
                # Reserve the slot first so a second run is rejected, then open the handle
                # inside the same handle lock: "_active is visible" then implies "handle ready".
                self._active[session_id] = run_id
            try:
                session = self.workspaces.repo_for(workspace).open(metadata)
            except SessionError as exc:
                with self._lock:
                    self._runs.pop(run_id, None)
                    self._active.pop(session_id, None)
                raise SessionReadError(f"打不开会话 {session_id}：{exc}") from exc
            with self._lock:
                self._sessions[run_id] = session

        logger.info("起运行 %s（会话 %s）", run_id, session_id)
        thread = threading.Thread(
            target=self._run,
            args=(
                record,
                workspace,
                session,
                prompt,
                auto_approve,
                chat or self.chat,
                branch,
                mode,
                full_ack,
            ),
            name=f"avid-run-{run_id}",
            daemon=True,
        )
        thread.start()
        return record

    def get(self, run_id: str) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunNotFound(f"没有这个运行：{run_id}")
        return record

    def active_run_id(self, session_id: str) -> str | None:
        with self._lock:
            return self._active.get(session_id)

    def active_session(self, session_id: str) -> Any | None:
        """Handle held by the active run, so read paths can reuse it instead of reopening."""
        run_id = self.active_run_id(session_id)
        if run_id is None:
            return None
        with self._lock:
            return self._sessions.get(run_id)

    def cancel(self, run_id: str) -> RunRecord:
        record = self.get(run_id)
        if record.terminal:
            raise RunFinished(f"运行已经结束：{run_id}（{record.status}）")
        record.cancel_requested = True
        record.cancel_reason = record.cancel_reason or "user"
        if record.state is not None:
            record.state.cancel(record.cancel_reason)
        with record.condition:
            record.condition.notify_all()
        logger.info("请求取消 %s", run_id)
        return record

    # ---- Event emission and subscription ----

    def emit(self, record: RunRecord, type: str, **data: Any) -> RunEvent:
        """Assign a seq and append to the buffer; only durable types get one."""
        with record.condition:
            seq = record.next_seq if type in DURABLE_SET else None
            if seq is not None:
                record.next_seq += 1
            event = RunEvent(
                type=type,
                data=data,
                run_id=record.run_id,
                seq=seq,
                ts=now_ms(),
            )
            record.events.append(event)
            if seq is not None:
                record.durable_index.append(record.dropped + len(record.events) - 1)
            self._trim(record, self.buffer_size, self.max_events)
            record.condition.notify_all()
        return event

    @staticmethod
    def _trim(
        record: RunRecord, size: int | None = None, max_events: int | None = None
    ) -> None:
        """Evict from the head under the condition: durable within size, all events within max."""
        # Only durable events count against the replay budget although deltas share this queue,
        # or a long reply's deltas would push them out and hand a reconnecting client a spurious
        # resync. Eviction stays a contiguous prefix, since absolute_index is computed as
        # dropped + len(events) and durable_index is maintained incrementally without a scan.
        limit = size if size is not None else 0
        if limit > 0:
            overflow = len(record.durable_index) - limit
            if overflow > 0:
                last_dropped_abs = record.durable_index[overflow - 1]
                cut_abs = last_dropped_abs + 1
                # Durable seqs are monotonic, so the last dropped durable event holds the max.
                dropped_event = record.events[last_dropped_abs - record.dropped]
                if dropped_event.seq is not None:
                    record.evicted_upto = max(record.evicted_upto, dropped_event.seq)
                cut = cut_abs - record.dropped
                del record.durable_index[:overflow]
                del record.events[:cut]
                record.dropped = cut_abs

        # Total-event cap: one long reply's deltas would otherwise grow the buffer to tens of
        # MB. Deltas may be dropped, so the oldest prefix goes first, and any durable event
        # dropped with it must advance evicted_upto or followers see a silent gap (I5).
        if max_events and len(record.events) > max_events:
            cut = len(record.events) - max_events
            head = record.dropped + cut
            while record.durable_index and record.durable_index[0] < head:
                absolute = record.durable_index.pop(0)
                dropped_event = record.events[absolute - record.dropped]
                if dropped_event.seq is not None:
                    record.evicted_upto = max(record.evicted_upto, dropped_event.seq)
            del record.events[:cut]
            record.dropped = head

    def _replay_from(
        self, record: RunRecord, after: int
    ) -> tuple[list[RunEvent], int, int]:
        """Return the events to send, the delivered cursor and the buffer index."""
        # A gap yields one durable resync instead of stale leftovers (I5); otherwise the buffer
        # is replayed with deltas excluded (I15) and events at or before the cursor skipped.
        if self._has_gap(record, after):
            resync = self.emit(record, RESYNC, after=after, reason="buffer_evicted")
            with record.condition:
                index = record.absolute_index()
            return [resync], max(after, resync.seq or after), index
        with record.condition:
            replayed = [
                event
                for event in record.events
                if event.type not in DELTA_EVENT_TYPES
                and (event.seq is None or event.seq > after)
            ]
            index = record.absolute_index()
        delivered = after
        for event in replayed:
            if event.seq is not None:
                delivered = max(delivered, event.seq)
        return replayed, delivered, index

    @staticmethod
    def _follow_snapshot_raw(
        record: RunRecord, delivered: int, index: int
    ) -> tuple[bool, list[RunEvent], int]:
        """Work out under the lock what to deliver next: gap flag, fresh events, new index."""
        # Both subscription paths require the caller to hold record.condition: the synchronous
        # one waits inside the same critical section, the async one clears its wake event there.
        if record.evicted_upto > delivered:
            return True, [], index
        if index < record.dropped:
            # Only droppable events (deltas, transients) were lost: continue from now.
            return False, [], record.absolute_index()
        fresh = list(record.events[index - record.dropped :])
        return False, fresh, record.absolute_index()

    def subscribe(
        self,
        run_id: str,
        *,
        after: int = 0,
        deltas: bool = False,
        heartbeat: float = STREAM_HEARTBEAT_SECONDS,
        stop: Callable[[], bool] | None = None,
    ) -> Iterator[RunEvent | None]:
        """Backfill from the cursor, then follow live; None marks one heartbeat."""
        # after is the highest durable seq the client knows; a cursor outside the buffer gets a
        # durable resync first so the client can rebuild (I5), and deltas are never replayed.
        # The gap is re-checked while following, because a slow consumer can fall out of the
        # buffer after subscribing; waiting uses condition.wait, so this runs in a thread.
        record = self.get(run_id)
        replayed, delivered, index = self._replay_from(record, after)
        for event in replayed:
            yield event

        while True:
            if stop is not None and stop():
                return
            with record.condition:
                stale, fresh, index = self._follow_snapshot_raw(record, delivered, index)
                if not fresh and not record.terminal and not stale:
                    # Snapshot and wait must share one critical section: wait releases and
                    # re-acquires the lock atomically, so an emit in between cannot lose its
                    # notify to a whole heartbeat.
                    record.condition.wait(timeout=heartbeat)
                    stale, fresh, index = self._follow_snapshot_raw(
                        record, delivered, index
                    )

            if stale:
                # A durable event was evicted before delivery: say so, then follow from the tail.
                logger.info("订阅 %s 的游标被缓冲淘汰，发 resync", run_id)
                gap_from = delivered
                resync = self.emit(
                    record, RESYNC, after=gap_from, reason="buffer_evicted"
                )
                delivered = max(delivered, resync.seq or delivered)
                with record.condition:
                    index = record.absolute_index()
                yield resync
                continue
            if not fresh:
                if record.terminal:
                    return
                yield None  # heartbeat
                continue
            for event in fresh:
                if event.type in DELTA_EVENT_TYPES and not deltas:
                    continue
                if event.seq is not None:
                    if event.seq <= delivered:
                        continue  # duplicate at the replay boundary: skip, do not redeliver
                    delivered = event.seq
                yield event
                if event.type in TERMINAL_EVENT_TYPES:
                    return

    # ---- Async subscription ----
    #
    # The synchronous subscriber occupies an anyio thread per SSE connection, so enough streams
    # starve the REST path. The async one waits on an asyncio event instead: a single bridge
    # thread per record wakes each subscriber loop, and replay, gap and resync stay identical.

    def _ensure_bridge(self, record: RunRecord) -> None:
        """Start the bridge thread lazily; it exits once terminal with nobody watching."""
        if record.bridge is not None and record.bridge.is_alive():
            return
        record.bridge = threading.Thread(
            target=self._bridge_loop,
            args=(record,),
            name=f"avid-watch-{record.run_id}",
            daemon=True,
        )
        record.bridge.start()

    @staticmethod
    def _bridge_loop(record: RunRecord) -> None:
        """Translate condition wakeups into asyncio events on each subscriber's loop."""
        while True:
            with record.condition:
                if not record.watchers:
                    if record.terminal:
                        record.bridge = None
                        return
                    # Nobody watching: sleep briefly and re-evaluate, since registering and
                    # removing a watcher both take this same lock and notify.
                    record.condition.wait(timeout=1.0)
                    continue
                record.condition.wait(timeout=STREAM_HEARTBEAT_SECONDS)
            for loop, wake in list(record.watchers):
                try:
                    loop.call_soon_threadsafe(wake.set)
                except RuntimeError:
                    continue  # loop already closed: the subscriber's finally detaches it

    async def subscribe_async(
        self,
        run_id: str,
        *,
        after: int = 0,
        deltas: bool = False,
        heartbeat: float = STREAM_HEARTBEAT_SECONDS,
    ) -> AsyncIterator[RunEvent | None]:
        """Async subscription that waits on the bridge instead of occupying a thread."""
        # None again marks a heartbeat. On the way out (terminal event delivered or generator
        # closed) the subscriber detaches its own watcher, and the bridge exits afterwards.
        record = self.get(run_id)
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        with record.condition:
            record.watchers.append((loop, wake))
            self._ensure_bridge(record)
        try:
            replayed, delivered, index = self._replay_from(record, after)
            for event in replayed:
                yield event

            while True:
                with record.condition:
                    stale, fresh, index = self._follow_snapshot_raw(
                        record, delivered, index
                    )
                    waiting = not fresh and not record.terminal and not stale
                    if waiting:
                        # Clearing under the same lock as the snapshot means a following emit
                        # is guaranteed to set the event, so no wakeup is lost.
                        wake.clear()
                if waiting:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(wake.wait(), timeout=heartbeat)
                    continue

                if stale:
                    logger.info("订阅 %s 的游标被缓冲淘汰，发 resync", run_id)
                    resync = self.emit(
                        record,
                        RESYNC,
                        after=delivered,
                        reason="buffer_evicted",
                    )
                    delivered = max(delivered, resync.seq or delivered)
                    with record.condition:
                        index = record.absolute_index()
                    yield resync
                    continue
                if not fresh:
                    if record.terminal:
                        return
                    yield None  # heartbeat
                    continue
                for event in fresh:
                    if event.type in DELTA_EVENT_TYPES and not deltas:
                        continue
                    if event.seq is not None:
                        if event.seq <= delivered:
                            continue
                        delivered = event.seq
                    yield event
                    if event.type in TERMINAL_EVENT_TYPES:
                        return
        finally:
            with record.condition:
                record.watchers = [
                    item for item in record.watchers if item[1] is not wake
                ]
                record.condition.notify_all()

    @staticmethod
    def _has_gap(record: RunRecord, after: int) -> bool:
        """True when durable events after the cursor have already been evicted."""
        with record.condition:
            return record.evicted_upto > after

    def streaming_chat(self, record: RunRecord) -> Callable[..., Any]:
        """Production chat: stream the call and forward text deltas onto the event stream."""
        # Only used when no chat was injected, since the scripted models in tests emit no
        # deltas. Summary calls bypass it, or summary text would merge into the real reply.
        def chat(config: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            return stream_completion(
                config,
                messages,
                on_delta=lambda text: self.emit_delta(record, self, text),
                on_reasoning=lambda text: self.emit_delta(
                    record, self, text, event_type=REASONING_DELTA
                ),
                **kwargs,
            )

        return chat

    @staticmethod
    def emit_delta(
        record: RunRecord,
        registry: "RunRegistry",
        text: str,
        *,
        event_type: str = ASSISTANT_DELTA,
    ) -> None:
        """Emit a delta: never replayed and never persisted, so only live subscribers see it."""
        # It travels the record.events queue all the same (subscribers wake on the same
        # condition), but cursor backfill skips it and _trim does not count it as durable.
        with record.condition:
            record.events.append(
                RunEvent(
                    type=event_type,
                    data={"text": text},
                    run_id=record.run_id,
                    seq=None,
                    ts=now_ms(),
                )
            )
            RunRegistry._trim(record, registry.buffer_size, registry.max_events)
            record.condition.notify_all()

    # ---- Run thread ----

    def _set_status(self, record: RunRecord, status: str) -> None:
        record.status = status
        with record.condition:
            record.condition.notify_all()

    def _run(
        self,
        record: RunRecord,
        workspace: Any,
        session: Any,
        prompt: str,
        auto_approve: bool,
        chat: Callable[..., Any] | None,
        branch: str = DEFAULT_BRANCH,
        permission: str | None = None,
        full_ack: bool = False,
    ) -> None:
        """Run thread; start already opened the session handle under the handle lock."""
        # The three axes and the sandbox state go into run_started: a refresh rebuilds the view
        # from that event rather than from the in-memory record, so "was the sandbox off" stays
        # a visible fact.
        safety = build_run_security(
            mode=permission or workspace.default_permission,
            root=workspace.root,
            run_tag=record.run_id,
            run_id=record.run_id,
            full_ack=full_ack,
            source="web",
        )
        state_spec = safety.summary()
        self.emit(
            record,
            RUN_STARTED,
            session_id=record.session_id,
            prompt=prompt,
            auto_approve=auto_approve,
            workspace=workspace.id,
            workspace_root=workspace.root,
            permission=safety.mode,
            approval=safety.approval,
            sandbox=safety.sandbox_policy,
            network=safety.network,
            sandbox_state=safety.sandbox.summary(),
            sandbox_notes=state_spec["notes"],
        )
        safety.audit_write(
            "run_start",
            session=record.session_id,
            workspace=workspace.id,
            prompt_chars=len(prompt),
        )
        # The recorder is built inside the try: if resolve_chat fails it never exists, and
        # _finish then has no usage to persist because record.recorder stays None.
        try:
            config = resolve_chat(model=record.model)
            recorder = SessionRecorder(session, branch)
            # Hand it to the record so _finish can persist usage before announcing the end.
            record.recorder = recorder
            recorder.ensure_branch()
            history = messages_for_branch(session, recorder.branch)
            messages = [*history, {"role": "user", "content": prompt}]

            # 命令分支：不调模型，结果以一条 assistant 条目收尾（SSE 生命周期不变）。
            if record.command == "compact":
                report = commands_module.compact_session(
                    history=list(history),
                    config=config,
                    summarize=chat_completion,
                    workspace_root=workspace.root,
                    on_compaction=recorder.record_compaction,
                )
                text = (
                    f"已压缩：{report.describe()}"
                    if report is not None
                    else "没有可压缩的更早历史（或摘要失败），会话保持不变"
                )
                self._message_sink(record, recorder)({"role": "assistant", "content": text})
                record.text = text
                self._finish(record, RUN_FINISHED, text=text, reason="command")
                return
            if record.command == "unknown":
                text = commands_module.help_text(workspace_root=workspace.root)
                self._message_sink(record, recorder)({"role": "assistant", "content": text})
                record.text = text
                self._finish(record, RUN_FINISHED, text=text, reason="command")
                return

            state = RunState.for_run(
                auto_approve=auto_approve,
                ask=record.approvals.request if record.approvals is not None else None,
                observer=lambda event: self._observe(record, event),
                permission_mode=permission or workspace.default_permission,
                workspace_root=workspace.root,
                # The very same spec as in run_started: the event and the enforcement agree.
                security=safety,
                # The utilization denominator follows this run's actual model configuration.
                context_window=config.context_window,
            )
            record.state = state
            # A cancel can land between thread start and this point, while record.state is
            # still None; the loop checks state.cancelled, so an unreplayed cancel would be
            # lost and the run would carry on to completion.
            if record.cancel_requested:
                state.cancel(record.cancel_reason or "user")

            # MCP servers live for the duration of the run: started here, closed in finally.
            # A server that fails to start is logged and skipped instead of blocking the run.
            mcp = McpManager(state.workspace_root)
            state.mcp = mcp
            for warning in mcp.start_all():
                logger.warning("run %s: %s", record.run_id, warning)

            # No injected chat means the production path: main rounds stream, summaries do not.
            streaming = chat is None
            mcp_schemas, mcp_impls = build_toolset(state)
            spec = RunSpec.resolve(
                config=config,
                chat=chat if chat is not None else self.streaming_chat(record),
                summarize=chat_completion if streaming else None,
                # An injected registry (tests, benchmarks) keeps its old meaning: no MCP tools.
                tools=TOOLS if self.tool_registry is not None else mcp_schemas,
                registry=self.tool_registry if self.tool_registry is not None else mcp_impls,
            )
            outcome = Run(
                messages,
                spec,
                state=state,
                on_message=self._message_sink(record, recorder),
                on_compaction=recorder.record_compaction,
            ).run()
            record.text = outcome.text
            self._finish(record, RUN_FINISHED, text=outcome.text, reason=outcome.reason)
        except RunCancelled as exc:
            record.cancel_reason = str(exc) or "cancelled"
            self._finish(record, RUN_CANCELLED, reason=record.cancel_reason)
        except LLMError as exc:
            self._fail(record, "llm_error", str(exc))
        except ConfigError as exc:
            self._fail(record, "config_error", str(exc))
        except SessionError as exc:
            self._fail(record, "session_error", str(exc))
        except Exception as exc:  # Programming errors must end visibly, not as a dead thread.
            logger.exception("运行 %s 内部错误", record.run_id)
            self._fail(record, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            if record.approvals is not None:
                record.approvals.close("run_ended")
            # MCP processes end with the run; record.state exists once assembly succeeded, so a
            # failure before that (an unreadable config, say) has nothing to close.
            if record.state is not None:
                record.state.close_mcp()
            # Detach the handle before closing it: a reader still using it has to finish first.
            with self.session_lock(record.session_id):
                with self._lock:
                    self._sessions.pop(record.run_id, None)
                    self._active.pop(record.session_id, None)
                if session is not None and not session.closed:
                    try:
                        session.close()
                    except SessionError:  # A failed close must not mask the run's result.
                        logger.warning(
                            "关闭会话 %s 失败", record.session_id, exc_info=True
                        )
            # The handle has been handed back, so the record keeps no reference to it: terminal
            # records stay for a while and would otherwise pin a closed session object.
            record.recorder = None

    def _observe(self, record: RunRecord, event: RunEvent) -> None:
        """Turn a kernel observer event into a registry event, tagging injected messages."""
        # Injected reminders are only tagged here and not emitted: the loop hands them to
        # on_message right after, and _message_sink emits the single durable event because only
        # it has the entry_id. Emitting in both places produced two notifications, one with
        # text and one blank, since each side carried half of the payload.
        if event.type == STOP_NUDGE:
            message = event.data.get("message")
            if isinstance(message, dict):
                record.injected[id(message)] = "nudge"
            return
        if event.type == RUN_STATUS and "subagent" not in event.data:
            # Round and token authority is state, while GET /runs/{id} reads RunRecord: without
            # this backfill REST would keep reporting round=0 and tokens=0 and only SSE would be
            # right. A subagent-marked status belongs to a child run and must not overwrite the
            # parent's figures; it still flows on as an event for the front end to fold in.
            if "round" in event.data:
                record.round = int(event.data["round"])
            if "tokens" in event.data:
                record.tokens = int(event.data["tokens"])
            # Usage likewise: SSE is live while a refresh reads REST, and the two must agree.
            if isinstance(event.data.get("usage"), dict):
                record.usage = event.data["usage"]
        self.emit(record, event.type, **event.data)

    def _message_sink(
        self, record: RunRecord, recorder: SessionRecorder
    ) -> Callable[[dict[str, Any]], None]:
        """Message channel -> persistence plus one durable message event."""

        def sink(message: dict[str, Any]) -> None:
            # Identify before persisting: injected nudges are stored as notices so the UI does
            # not render them as words the user spoke, since their text comes from a hook.
            label = record.injected.pop(id(message), None)
            entry_id = recorder.on_message(message, notice=label is not None)
            if label == "nudge":
                type = STOP_NUDGE
            else:
                type = _MESSAGE_EVENTS.get(str(message.get("role")), "")
            if not type:
                return
            payload: dict[str, Any] = {"entry_id": entry_id, "message": message}
            if label is not None:
                # The one emission for reminder events: the loop's own only tags them, and the
                # content is added here while the event type stays what consumers expect.
                payload["content"] = str(message.get("content") or "")
            self.emit(record, type, **payload)

        return sink

    def _finish(self, record: RunRecord, type: str, **data: Any) -> None:
        status = (
            "finished"
            if type == RUN_FINISHED
            else "cancelled"
            if type == RUN_CANCELLED
            else "failed"
        )
        record.finished_at = now_ms()
        # Terminal states carry the usage snapshot too: run_finished is durable, so a subscriber
        # gets the final reading without waiting for reconciliation, and cancel and failure use
        # context just the same.
        if record.state is not None:
            record.usage = record.state.usage_report()
            data.setdefault("usage", record.usage)
        # Persist before announcing: a client that sees run_finished refetches the branch list,
        # and that read has to already include this run's numbers, or the UI stays on the
        # previous run's figures. The write is slow (session write plus fsync), so it happens
        # before any state flips, as in the block below.
        self._persist_usage(record)
        with record.condition:
            # Status and terminal event change inside one critical section. Subscribers decide
            # whether more events are coming from record.terminal under the same condition, so
            # splitting the two would show them a terminal status with no terminal event
            # buffered and make them return as if the stream had ended: a silent gap.
            record.status = status
            self.emit(record, type, **data)
        logger.info("运行 %s 结束：%s", record.run_id, record.status)
        self._sweep()

    def _persist_usage(self, record: RunRecord) -> None:
        """Write this run's usage snapshot into the session values, per branch and replacing."""
        # All three terminal states write, because cancel and failure consume context too and a
        # user coming back should see where the last run stopped rather than an older success.
        # No reading means no write: a provider that omits the usage option reports all-null, and
        # storing that would make "no data" look like "ran once without usage".
        state = record.state
        recorder = record.recorder
        if recorder is None or state is None:
            return
        report = state.usage_report()
        if report["context"]["tokens"] is None and report["compaction"]["count"] == 0:
            return
        record.usage = report
        try:
            recorder.record_usage(report)
        except SessionError:  # A failed write must not mask the run's result.
            logger.warning("会话 %s 的 usage 落库失败", record.session_id, exc_info=True)

    def _sweep(self) -> None:
        """Reclaim terminal records and handle locks that are no longer needed."""
        # Without it a long-lived server accumulates: each record holds up to buffer_size durable
        # events plus all deltas of that period, and every session ever visited keeps a lock.
        # Two rules: drop past the retention window, and once over the count cap drop
        # oldest-first but never a just-finished record, whose buffer a subscriber may still be
        # consuming, since dropping it is the silent gap I5 forbids.
        now = now_ms()
        with self._lock:
            victims = [
                run_id
                for run_id, record in self._runs.items()
                if record.terminal
                and record.finished_at is not None
                and now - record.finished_at > self._retention_ms
            ]
            extra = len(self._runs) - len(victims) - self._max_runs
            if extra > 0:
                old_enough = [
                    record
                    for record in self._runs.values()
                    if record.terminal
                    and record.finished_at is not None
                    and record.run_id not in victims
                    and now - record.finished_at > int(SWEEP_MIN_AGE_SECONDS * 1000)
                ]
                old_enough.sort(key=lambda record: record.finished_at or 0)
                victims.extend(record.run_id for record in old_enough[:extra])

            for run_id in victims:
                record = self._runs.pop(run_id, None)
                if record is not None:
                    with record.condition:
                        record.events.clear()
                        record.durable_index.clear()
            for session_id in [
                session_id
                for session_id in self._session_locks
                if session_id not in self._session_lock_users
                and session_id not in self._active
            ]:
                del self._session_locks[session_id]
        if victims:
            logger.info("回收 %d 条已结束的运行记录", len(victims))

    def _fail(self, record: RunRecord, code: str, message: str) -> None:
        record.error = {"code": code, "message": message}
        self._finish(record, RUN_FAILED, code=code, message=message)

    # ---- Session lookup ----

    def find_metadata(self, session_id: str) -> SessionMetadata | None:
        """Session metadata, found across workspaces because sessions do not record one."""
        found = self.workspaces.find_session(session_id)
        return None if found is None else found[1]


__all__ = [
    "MAX_EVENT_BUFFER",
    "MAX_RETAINED_RUNS",
    "REPLAY_BUFFER_SIZE",
    "SESSION_DIR",
    "RunRecord",
    "RunRegistry",
]
