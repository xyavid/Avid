"""运行注册表：把 ``agent_loop`` 包成一次可观察、可重放的运行（F1）。

职责边界（设计文档 §3.1）：

* **生命周期**：一个会话同时至多一个活动 run（不变量 I3，仅进程内）；工作线程
  跑循环，注册表只做编排——它**不重写循环**，``RoundLimitExceeded`` / ``LLMError``
  只捕获并映射成 ``run_failed{code}``（A5）。
* **事件与重放**：唯一发射线程分配 ``seq``，有界缓冲保存 durable 与 transient
  事件；游标落在缓冲之外时发 ``resync``，**不允许静默缺口**（I5）。
* **审批**：把 ``ApprovalTable.request`` 注入 ``agent_loop(ask=...)``。
* **取消**：只置位；循环在步骤边界抛出 ``RunCancelled``（I9）。

它不 import FastAPI，也不 import ``web/``（A4）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ..ai.client import LLMError, chat_completion, stream_completion
from ..ai.config import ConfigError, load_config
from ..runtime import events
from ..runtime.events import RunEvent
from ..runtime.loop import RoundLimitExceeded, RunCancelled, agent_loop
from ..runtime.state import RunState
from ..workspaces import SESSION_DIR
from ..session import (
    DEFAULT_BRANCH,
    JsonlSessionMetadata,
    JsonlSessionRepo,
    SessionError,
    SessionRecorder,
    messages_for_branch,
)
from ..tools import workspace
from .approvals import APPROVAL_TIMEOUT_SECONDS, ApprovalTable
from .errors import RunBusy, RunFinished, RunNotFound, SessionNotFound
from .workspaces import WorkspaceService

logger = logging.getLogger("avid.svc.runs")

# 每个 run 保留的有界重放缓冲。淘汰即 resync（I5）。
REPLAY_BUFFER_SIZE = 512

# 事件流静默兜底：客户端这么久没收到东西后应与注册表对账（I13）。
TERMINAL_HARD_FALLBACK_SECONDS = 30.0

# 消息角色 → durable 消息事件
_MESSAGE_EVENTS = {
    "user": events.USER_MESSAGE,
    "assistant": events.ASSISTANT_MESSAGE,
    "tool": events.TOOL_RESULT_MESSAGE,
}


@dataclass
class RunRecord:
    """一次运行的实时视图。字段由注册表与运行线程读写。"""

    run_id: str
    session_id: str
    started_at: int
    status: str = "running"  # running | awaiting_approval | finished | failed | cancelled
    round: int = 0
    tokens: int = 0
    text: str = ""
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    finished_at: int | None = None

    # 事件缓冲：durable 与 transient 都在里面；delta 不落缓冲（I15）。
    events: list[RunEvent] = field(default_factory=list)
    dropped: int = 0  # 从队首淘汰的条数；绝对下标 = dropped + 位置
    # 已被淘汰的 durable 事件的最高 seq。用它判断「游标是否落在缓冲之外」，
    # 比看队首更稳：队首可能是一条 transient 事件。
    evicted_upto: int = 0
    next_seq: int = 1

    condition: threading.Condition = field(default_factory=threading.Condition)
    approvals: ApprovalTable | None = None
    state: RunState | None = None
    # 注入消息的标注：id(message) → "todo" | "nudge"。由 on_event 先标、on_message 后取。
    injected: dict[int, str] = field(default_factory=dict)

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
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "cancel_reason": self.cancel_reason,
            "pending_approvals": [item.to_dict() for item in pending],
        }


class RunRegistry:
    """进程内的运行注册表。每个会话至多一个活动 run。"""

    def __init__(
        self,
        workspaces: WorkspaceService,
        *,
        chat: Callable[..., Any] | None = None,
        tool_registry: dict[str, Any] | None = None,
        buffer_size: int = REPLAY_BUFFER_SIZE,
        approval_timeout: float = APPROVAL_TIMEOUT_SECONDS,
    ) -> None:
        self.workspaces = workspaces
        self.chat = chat
        # 工具注册表可注入：测试要一个不真的执行 shell 的 bash 工具，
        # 生产路径留空 = 用 tools/ 的默认注册表。
        self.tool_registry = tool_registry
        self.buffer_size = buffer_size
        self.approval_timeout = approval_timeout
        self._runs: dict[str, RunRecord] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()
        self._sessions: dict[str, Any] = {}  # run_id -> 运行期打开的会话句柄

    # ---------------- 生命周期 ----------------

    def start(
        self,
        session_id: str,
        prompt: str,
        *,
        auto_approve: bool = False,
        chat: Callable[..., Any] | None = None,
        branch: str = DEFAULT_BRANCH,
        permission: str | None = None,
    ) -> RunRecord:
        """登记并起线程。已占用 → ``RunBusy``；会话不存在 → ``SessionNotFound``。

        ``branch`` 决定这次运行追加到哪条链上：历史取该分支的链，新消息接在它的链尾。
        ``permission`` 是这次运行的权限模式；缺省按**会话所属工作区的默认权限**。
        """
        found = self.workspaces.find_session(session_id)
        if found is None:
            raise SessionNotFound(f"没有这个会话：{session_id}")
        workspace, metadata = found
        mode = permission or workspace.default_permission

        with self._lock:
            if session_id in self._active:
                raise RunBusy(f"会话已有活动 run：{self._active[session_id]}")
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            record = RunRecord(
                run_id=run_id, session_id=session_id, started_at=events.now_ms()
            )
            record.approvals = ApprovalTable(
                emit=lambda type, **data: self.emit(record, type, **data),
                set_status=lambda status: self._set_status(record, status),
                is_cancelled=lambda: record.cancel_requested,
                timeout=self.approval_timeout,
            )
            self._runs[run_id] = record
            self._active[session_id] = run_id

        logger.info("起运行 %s（会话 %s）", run_id, session_id)
        thread = threading.Thread(
            target=self._run,
            args=(
                record,
                workspace,
                metadata,
                prompt,
                auto_approve,
                chat or self.chat,
                branch,
                mode,
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
        """活动 run 正持有的会话句柄（供只读路径复用，避免二次 open）。"""
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

    # ---------------- 事件发射与订阅 ----------------

    def emit(self, record: RunRecord, type: str, **data: Any) -> RunEvent:
        """分配 seq 并入缓冲。durable 带 seq，transient/delta 不带（§5.2）。"""
        with record.condition:
            seq = record.next_seq if type in events.DURABLE_SET else None
            if seq is not None:
                record.next_seq += 1
            event = RunEvent(
                type=type,
                data=data,
                run_id=record.run_id,
                seq=seq,
                ts=events.now_ms(),
            )
            record.events.append(event)
            self._trim(record, self.buffer_size)
            record.condition.notify_all()
        return event

    @staticmethod
    def _trim(record: RunRecord, size: int | None = None) -> None:
        """淘汰队首，使剩下的 **durable** 事件不超过上限。调用方必须持有 condition。

        只数 durable：设计里的缓冲是「最近 512 条 **durable** 事件」（§5.1），而 delta
        与 durable 走的是同一条实时队列。若按总条数淘汰，一次长回复的上千条 delta 会把
        durable 事件挤出缓冲，重连的客户端就会平白收到 ``resync``（I5 的语义被 delta
        的多少左右，这显然不对）。

        淘汰必须是**连续前缀**：``absolute_index`` 用 ``dropped + len(events)`` 定位实时
        队列，非连续删除会让这个下标算错。因此先找到第 overflow 条 durable，把包括它
        在内的前缀整段丢掉（夹在其中的 delta 一起丢——它们本来就不参与重放）。
        """

        limit = size if size is not None else 0
        if limit <= 0:
            return
        durable_positions = [
            index for index, event in enumerate(record.events) if event.seq is not None
        ]
        overflow = len(durable_positions) - limit
        if overflow <= 0:
            return
        cut = durable_positions[overflow - 1] + 1
        for dropped in record.events[:cut]:
            if dropped.seq is not None:
                record.evicted_upto = max(record.evicted_upto, dropped.seq)
        del record.events[:cut]
        record.dropped += cut

    def subscribe(
        self,
        run_id: str,
        *,
        after: int = 0,
        deltas: bool = False,
        heartbeat: float = 15.0,
        stop: Callable[[], bool] | None = None,
    ) -> Iterator[RunEvent | None]:
        """按游标补齐 + 实时跟随。``None`` 表示一次心跳。

        ``after`` 是客户端已知的最大 durable ``seq``；游标落在缓冲之外时先发一条
        durable ``resync``，客户端据此重建视图（I5）。delta 不重放（I15）。
        """
        record = self.get(run_id)
        index = 0

        if self._has_gap(record, after):
            # 缺口已经存在：先显式告知，再从当前队尾开始跟随（不补发残存的旧事件，
            # 否则客户端会在重建视图的同时收到更小的 seq）。
            yield self.emit(record, events.RESYNC, after=after, reason="buffer_evicted")
            with record.condition:
                index = record.absolute_index()
        else:
            with record.condition:
                replayed = list(record.events)
                index = record.absolute_index()
            for event in replayed:
                if event.type in events.DELTA_EVENT_TYPES:
                    continue
                if event.seq is not None and event.seq <= after:
                    continue
                yield event

        while True:
            if stop is not None and stop():
                return
            with record.condition:
                fresh = list(record.events[max(0, index - record.dropped) :])
                index = record.absolute_index()
                if not fresh and not record.terminal:
                    record.condition.wait(timeout=heartbeat)
                    fresh = list(record.events[max(0, index - record.dropped) :])
                    index = record.absolute_index()
            if not fresh:
                if record.terminal:
                    return
                yield None  # 心跳
                continue
            for event in fresh:
                if event.type in events.DELTA_EVENT_TYPES and not deltas:
                    continue
                yield event
                if event.type in events.TERMINAL_EVENT_TYPES:
                    return

    @staticmethod
    def _has_gap(record: RunRecord, after: int) -> bool:
        """``after`` 之后是否已经有 durable 事件被缓冲淘汰。"""
        with record.condition:
            return record.evicted_upto > after

    def streaming_chat(self, record: RunRecord) -> Callable[..., Any]:
        """生产路径的 chat：流式调用，并把正文增量接到事件流上。

        只在**没有注入 chat** 时使用：测试注入的脚本模型不产生增量，也不需要。
        摘要调用不走这里——``agent_loop`` 的 ``summarize`` 参数把两者分开，否则摘要
        文本会与真正的回复粘成同一条乐观气泡。
        """

        def chat(config: Any, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
            return stream_completion(
                config,
                messages,
                on_delta=lambda text: self.emit_delta(record, self, text),
                **kwargs,
            )

        return chat

    @staticmethod
    def emit_delta(record: RunRecord, registry: "RunRegistry", text: str) -> None:
        """delta 不进重放、不进会话：只让**当前**订阅者看到（I15）。

        它仍走 ``record.events`` 这条实时队列（订阅者靠同一把 condition 被唤醒），但
        游标补齐会跳过它，`_trim` 也不把它算进重放预算。
        """
        with record.condition:
            record.events.append(
                RunEvent(
                    type=events.ASSISTANT_DELTA,
                    data={"text": text},
                    run_id=record.run_id,
                    seq=None,
                    ts=events.now_ms(),
                )
            )
            RunRegistry._trim(record, registry.buffer_size)
            record.condition.notify_all()

    # ---------------- 运行线程 ----------------

    def _set_status(self, record: RunRecord, status: str) -> None:
        record.status = status
        with record.condition:
            record.condition.notify_all()

    def _run(
        self,
        record: RunRecord,
        workspace: Any,
        metadata: JsonlSessionMetadata,
        prompt: str,
        auto_approve: bool,
        chat: Callable[..., Any] | None,
        branch: str = DEFAULT_BRANCH,
        permission: str | None = None,
    ) -> None:
        session = None
        # run_started 带上归属与模式：刷新页面后重建界面靠它，而不是靠内存里的 RunRecord。
        self.emit(
            record,
            events.RUN_STARTED,
            session_id=record.session_id,
            prompt=prompt,
            auto_approve=auto_approve,
            workspace=workspace.id,
            workspace_root=workspace.root,
            permission=permission or workspace.default_permission,
        )
        try:
            config = load_config()
            session = self.workspaces.repo_for(workspace).open(metadata)
            with self._lock:
                self._sessions[record.run_id] = session
            recorder = SessionRecorder(session, branch)
            recorder.ensure_branch()
            history = messages_for_branch(session, recorder.branch)
            messages = [*history, {"role": "user", "content": prompt}]

            state = RunState.for_run(
                auto_approve=auto_approve,
                ask=record.approvals.request if record.approvals is not None else None,
                observer=lambda event: self._observe(record, event),
                permission_mode=permission or workspace.default_permission,
                workspace_root=workspace.root,
            )
            record.state = state

            # 没有注入 chat = 生产路径：主轮次流式、摘要非流式（见 agent_loop 的 summarize）。
            streaming = chat is None
            text = agent_loop(
                messages,
                config=config,
                chat=self.streaming_chat(record) if streaming else chat,
                summarize=chat_completion if streaming else None,
                auto_approve=auto_approve,
                on_message=self._message_sink(record, recorder),
                state=state,
                registry=self.tool_registry,
            )
            record.text = text
            self._finish(record, events.RUN_FINISHED, text=text)
        except RunCancelled as exc:
            record.cancel_reason = str(exc) or "cancelled"
            self._finish(record, events.RUN_CANCELLED, reason=record.cancel_reason)
        except RoundLimitExceeded as exc:
            self._fail(record, "round_limit", str(exc))
        except LLMError as exc:
            self._fail(record, "llm_error", str(exc))
        except ConfigError as exc:
            self._fail(record, "config_error", str(exc))
        except SessionError as exc:
            self._fail(record, "session_error", str(exc))
        except Exception as exc:  # 程序错误也要变成可观察的终态，而不是静默死线程
            logger.exception("运行 %s 内部错误", record.run_id)
            self._fail(record, "internal", f"{type(exc).__name__}: {exc}")
        finally:
            if record.approvals is not None:
                record.approvals.close("run_ended")
            with self._lock:
                self._sessions.pop(record.run_id, None)
                self._active.pop(record.session_id, None)
            if session is not None and not session.closed:
                try:
                    session.close()
                except SessionError:  # 关闭失败不该掩盖运行结果
                    logger.warning("关闭会话 %s 失败", record.session_id, exc_info=True)

    def _observe(self, record: RunRecord, event: RunEvent) -> None:
        """内核观察者 → 注册表事件：补上 run_id/seq/ts，并标注注入消息。"""
        if event.type in (events.TODO_REMINDER, events.STOP_NUDGE):
            message = event.data.get("message")
            if isinstance(message, dict):
                record.injected[id(message)] = (
                    "todo" if event.type == events.TODO_REMINDER else "nudge"
                )
        self.emit(record, event.type, **event.data)

    def _message_sink(
        self, record: RunRecord, recorder: SessionRecorder
    ) -> Callable[[dict[str, Any]], None]:
        """消息通道 → 落库 + durable 消息事件。

        ``on_message`` 仍是消息的唯一出口（§7.1）。注入的 TODO 提醒 / Stop nudge
        由循环**先**发 ``on_event``，这里按对象身份认出它们，而不是解析文本前缀
        （§5.3）；``entry_id`` 来自 ``SessionRecorder``（唯一落库点，I2）。
        """

        def sink(message: dict[str, Any]) -> None:
            entry_id = recorder.on_message(message)
            label = record.injected.pop(id(message), None)
            if label == "todo":
                type = events.TODO_REMINDER
            elif label == "nudge":
                type = events.STOP_NUDGE
            else:
                type = _MESSAGE_EVENTS.get(str(message.get("role")), "")
            if not type:
                return
            self.emit(record, type, entry_id=entry_id, message=message)

        return sink

    def _finish(self, record: RunRecord, type: str, **data: Any) -> None:
        record.status = (
            "finished"
            if type == events.RUN_FINISHED
            else "cancelled"
            if type == events.RUN_CANCELLED
            else "failed"
        )
        record.finished_at = events.now_ms()
        self.emit(record, type, **data)
        logger.info("运行 %s 结束：%s", record.run_id, record.status)

    def _fail(self, record: RunRecord, code: str, message: str) -> None:
        record.error = {"code": code, "message": message}
        self._finish(record, events.RUN_FAILED, code=code, message=message)

    # ---------------- 会话定位 ----------------

    def find_metadata(self, session_id: str) -> JsonlSessionMetadata | None:
        """会话元信息（跨工作区找）。会话本身不带工作区信息，所以只能逐个库扫。"""
        found = self.workspaces.find_session(session_id)
        return None if found is None else found[1]


__all__ = [
    "REPLAY_BUFFER_SIZE",
    "SESSION_DIR",
    "TERMINAL_HARD_FALLBACK_SECONDS",
    "RunRecord",
    "RunRegistry",
]
