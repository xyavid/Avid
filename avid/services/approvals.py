"""待决表：挂起等待人类的两件事——毁灭级命令的裁决（approval）与模型的提问（question）。

两者共用一张表和同一套「挂起 → 超时 → 取消 → run 结束」的语义，因为它们在界面上是
同一件事：**这个运行卡住了，等你说句话**。区别只在结果：审批是 allow/deny（超时按拒绝
收场），提问是一条文本答案（超时是「没答」，不是「拒绝」——模型据此降级，而不是当被否决）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..agent.events import APPROVAL_REQUESTED, APPROVAL_RESOLVED, now_ms
from .errors import ApprovalConflict, ApprovalExpired, ApprovalNotFound

logger = logging.getLogger("avid.services.approvals")

# Seconds to wait for an answer; it must stay below the subagent batch budget, or the child run
# would be killed before the approval can ever arrive.
APPROVAL_TIMEOUT_SECONDS = 120.0

# Wait granularity: short hops let the waiter notice cancellation and shutdown promptly.
_POLL_SECONDS = 0.2

# Answer values: "allow" / "deny" for approvals, "answered" / "unanswered" for questions.
Decision = str

#: Two kinds of pending work, sharing one table and one UI slot.
KIND_APPROVAL = "approval"
KIND_QUESTION = "question"
#: Longest question and option the table accepts (a wall of text is not a question).
MAX_QUESTION_CHARS = 500
MAX_OPTION_CHARS = 80
MAX_OPTIONS = 6


@dataclass
class PendingApproval:
    """One unanswered approval request; the timestamps are epoch milliseconds."""

    id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    created_at: int
    expires_at: int
    #: "approval" waits for allow/deny; "question" waits for a text answer (阶段 58)。
    kind: str = KIND_APPROVAL
    #: For questions: the choices offered to the human (empty = free text).
    options: tuple[str, ...] = ()
    decision: Decision | None = None
    #: For questions: what the human typed or chose; None means nobody answered.
    answer: str | None = None
    resolved_at: int | None = None
    resolved_reason: str | None = None
    expired: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.id,
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "kind": self.kind,
            "options": list(self.options),
            "decision": self.decision,
            "answer": self.answer,
            "resolved_at": self.resolved_at,
            "resolved_reason": self.resolved_reason,
        }


@dataclass(frozen=True)
class Resolution:
    """Outcome of one answer; ``accepted=False`` means the answer was already recorded."""

    accepted: bool
    decision: Decision
    reason: str


@dataclass
class _Decided:
    """A settled item kept in memory so that repeated answers stay idempotent."""

    decision: Decision
    reason: str
    expired: bool = False
    #: 提问的答案文本：再答一次要能分辨「同一个答案」与「换了个答案」（后者是冲突）。
    answer: str | None = None


@dataclass
class ApprovalTable:
    """Per-run approval table; the injected callbacks report events, status and cancellation."""

    emit: Callable[..., object]
    set_status: Callable[[str], None]
    is_cancelled: Callable[[], bool]
    timeout: float = APPROVAL_TIMEOUT_SECONDS
    # Identifier shape handed to the client, which echoes it back when answering.
    new_id: Callable[[], str] = field(
        default=lambda: f"ap_{uuid.uuid4().hex[:12]}"
    )
    # Guards both maps below and wakes up waiters when an answer arrives.
    _condition: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )
    # Unanswered requests by id; settled ones move to the decided map.
    _pending: dict[str, PendingApproval] = field(default_factory=dict, repr=False)
    _decided: dict[str, _Decided] = field(default_factory=dict, repr=False)
    # Set when the run ends, so every waiter falls through to a denial.
    _closed: bool = False

    # Called from the run thread while a tool waits for its answer.

    def request(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        """Blocking ask callback: waits for an answer and returns whether the tool may run."""
        # Refuse before allocating an id when the run is already gone.
        if self.is_cancelled() or self._closed:
            return False

        pending = PendingApproval(
            id=self.new_id(),
            tool=name,
            arguments=arguments,
            reason=reason,
            created_at=now_ms(),
            expires_at=now_ms() + int(self.timeout * 1000),
        )
        with self._condition:
            # The run may have ended while the pending entry was being built.
            if self._closed_now():
                return False
            self._pending[pending.id] = pending
        self.set_status("awaiting_approval")
        self.emit(
            APPROVAL_REQUESTED,
            approval_id=pending.id,
            tool=name,
            arguments=arguments,
            reason=reason,
            created_at=pending.created_at,
            expires_at=pending.expires_at,
        )

        decision, why = self._await(pending)

        self.emit(
            APPROVAL_RESOLVED,
            approval_id=pending.id,
            tool=name,
            decision=decision,
            reason=why,
        )
        self.set_status("running")
        logger.info("审批 %s → %s（%s）", pending.id, decision, why)
        return decision == "allow"

    def ask(self, question: str, options: tuple[str, ...] = ()) -> str | None:
        """Blocking question callback: returns the human's answer, or None when nobody answered.

        None 覆盖超时、取消、run 结束与「这个进程根本没有提问通道」——对模型来说它们是
        同一件事：**没有答案**，据此降级即可，而不是把它当成被否决。
        """
        text = " ".join(str(question).split())[:MAX_QUESTION_CHARS]
        if not text:
            return None
        choices = tuple(
            " ".join(str(item).split())[:MAX_OPTION_CHARS]
            for item in list(options)[:MAX_OPTIONS]
            if str(item).strip()
        )
        if self.is_cancelled() or self._closed:
            return None

        pending = PendingApproval(
            id=self.new_id(),
            tool="ask_user",
            arguments={"question": text, "options": list(choices)},
            reason=text,
            created_at=now_ms(),
            expires_at=now_ms() + int(self.timeout * 1000),
            kind=KIND_QUESTION,
            options=choices,
        )
        with self._condition:
            if self._closed_now():
                return None
            self._pending[pending.id] = pending
        self.set_status("awaiting_input")
        self.emit(
            APPROVAL_REQUESTED,
            approval_id=pending.id,
            tool="ask_user",
            arguments=pending.arguments,
            reason=text,
            kind=KIND_QUESTION,
            options=list(choices),
            created_at=pending.created_at,
            expires_at=pending.expires_at,
        )

        decision, why = self._await(pending)
        self.emit(
            APPROVAL_RESOLVED,
            approval_id=pending.id,
            tool="ask_user",
            decision=decision,
            reason=why,
            answer=pending.answer,
        )
        self.set_status("running")
        logger.info("提问 %s → %s（%s）", pending.id, decision, why)
        return pending.answer

    def answer(self, approval_id: str, text: str) -> Resolution:
        """Answer one question: unknown ids are 404, expired ones 410, repeats are idempotent."""
        value = str(text)[:MAX_QUESTION_CHARS]
        with self._condition:
            pending = self._pending.get(approval_id)
            if pending is not None:
                if pending.kind != KIND_QUESTION:
                    raise ApprovalConflict(f"这一条不是提问：{approval_id}")
                pending.answer = value
                self._finish(pending, "answered", "user")
                self._condition.notify_all()
                return Resolution(accepted=True, decision="answered", reason="user")

            previous = self._decided.get(approval_id)
            if previous is None:
                raise ApprovalNotFound(f"没有这个待决项：{approval_id}")
            if previous.expired:
                raise ApprovalExpired(f"提问已过期：{approval_id}")
            if previous.decision != "answered":
                raise ApprovalConflict(f"这一条已经以 {previous.decision} 结束：{approval_id}")
            if previous.answer != value:
                raise ApprovalConflict(f"这一条已经答过了：{approval_id}")
            return Resolution(accepted=False, decision=previous.decision, reason=previous.reason)

    def _await(self, pending: PendingApproval) -> tuple[Decision, str]:
        """Wait for an answer, a cancellation, the run ending or expiry, whichever comes first."""
        # 提问的「有人作答」是 answer 有值；审批是 decision 有值。两者的失败收场共用一套：
        # 审批按拒绝（deny），提问按「没答」（unanswered）——前者是被否决，后者是没消息。
        fallback = "unanswered" if pending.kind == KIND_QUESTION else "deny"

        def settled() -> bool:
            return pending.decision is not None or pending.answer is not None

        with self._condition:
            while not settled():
                if self._closed:
                    self._finish(pending, fallback, "run_ended")
                    break
                if self.is_cancelled():
                    self._finish(pending, fallback, "cancelled")
                    break
                remaining = pending.expires_at - now_ms()
                if remaining <= 0:
                    # Mark expiry before settling, so a late answer can be rejected as expired.
                    pending.expired = True
                    self._finish(pending, fallback, "timeout")
                    break
                self._condition.wait(min(remaining / 1000.0, _POLL_SECONDS))
            return pending.decision or fallback, pending.resolved_reason or "unknown"

    # Called from the request-handling thread when an answer arrives.

    def resolve(self, approval_id: str, decision: Decision) -> Resolution:
        """Answer one approval: unknown ids are 404, expired ones 410, repeats are idempotent."""
        if decision not in ("allow", "deny"):
            from .errors import InvalidRequest

            raise InvalidRequest(f"decision 只能是 allow 或 deny，收到 {decision!r}")

        with self._condition:
            pending = self._pending.get(approval_id)
            if pending is not None:
                if pending.kind != KIND_APPROVAL:
                    raise ApprovalConflict(f"这一条不是审批：{approval_id}")
                self._finish(pending, decision, "user")
                self._condition.notify_all()
                return Resolution(accepted=True, decision=decision, reason="user")

            previous = self._decided.get(approval_id)
            if previous is None:
                raise ApprovalNotFound(f"没有这个待决审批：{approval_id}")
            # Expiry is reported on its own so the client can tell a timeout from an unknown id.
            if previous.expired:
                raise ApprovalExpired(f"审批已过期：{approval_id}")
            if previous.decision != decision:
                # A different answer is a conflict; the same answer is idempotent.
                raise ApprovalConflict(
                    f"审批已经以 {previous.decision} 结束：{approval_id}"
                )
            return Resolution(
                accepted=False, decision=previous.decision, reason=previous.reason
            )

    def _closed_now(self) -> bool:
        """Re-read the closed flag under the lock, since another thread may have closed it."""
        return self._closed

    def pending(self) -> list[PendingApproval]:
        with self._condition:
            return list(self._pending.values())

    def close(self, reason: str = "run_ended") -> None:
        """End the table: every waiter wakes up and settles as denied."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    # Internal helpers.

    def _finish(self, pending: PendingApproval, decision: Decision, reason: str) -> None:
        """Settle one approval while the condition lock is held."""
        pending.decision = decision
        pending.resolved_at = now_ms()
        pending.resolved_reason = reason
        self._pending.pop(pending.id, None)
        self._decided[pending.id] = _Decided(
            decision=decision,
            reason=reason,
            expired=pending.expired,
            answer=pending.answer,
        )


__all__ = [
    "APPROVAL_TIMEOUT_SECONDS",
    "KIND_APPROVAL",
    "KIND_QUESTION",
    "MAX_OPTIONS",
    "ApprovalTable",
    "PendingApproval",
    "Resolution",
]
