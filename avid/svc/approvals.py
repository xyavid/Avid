"""Pending-approval table: blocking approvals behind the network boundary, failing closed."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..runtime import events
from .errors import ApprovalConflict, ApprovalExpired, ApprovalNotFound

logger = logging.getLogger("avid.svc.approvals")

# Seconds to wait for an answer; it must stay below the subagent batch budget, or the child run
# would be killed before the approval can ever arrive.
APPROVAL_TIMEOUT_SECONDS = 120.0

# Wait granularity: short hops let the waiter notice cancellation and shutdown promptly.
_POLL_SECONDS = 0.2

# Answer values, either "allow" or "deny".
Decision = str


@dataclass
class PendingApproval:
    """One unanswered approval request; the timestamps are epoch milliseconds."""

    id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    created_at: int
    expires_at: int
    decision: Decision | None = None
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
            "decision": self.decision,
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
    """A settled approval kept in memory so that repeated answers stay idempotent."""

    decision: Decision
    reason: str
    expired: bool = False


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
            created_at=events.now_ms(),
            expires_at=events.now_ms() + int(self.timeout * 1000),
        )
        with self._condition:
            # The run may have ended while the pending entry was being built.
            if self._closed_now():
                return False
            self._pending[pending.id] = pending
        self.set_status("awaiting_approval")
        self.emit(
            events.APPROVAL_REQUESTED,
            approval_id=pending.id,
            tool=name,
            arguments=arguments,
            reason=reason,
            created_at=pending.created_at,
            expires_at=pending.expires_at,
        )

        decision, why = self._await(pending)

        self.emit(
            events.APPROVAL_RESOLVED,
            approval_id=pending.id,
            tool=name,
            decision=decision,
            reason=why,
        )
        self.set_status("running")
        logger.info("审批 %s → %s（%s）", pending.id, decision, why)
        return decision == "allow"

    def _await(self, pending: PendingApproval) -> tuple[Decision, str]:
        """Wait for an answer, a cancellation, the run ending or expiry, whichever comes first."""
        with self._condition:
            while pending.decision is None:
                if self._closed:
                    self._finish(pending, "deny", "run_ended")
                    break
                if self.is_cancelled():
                    self._finish(pending, "deny", "cancelled")
                    break
                remaining = pending.expires_at - events.now_ms()
                if remaining <= 0:
                    # Mark expiry before settling, so a late answer can be rejected as expired.
                    pending.expired = True
                    self._finish(pending, "deny", "timeout")
                    break
                self._condition.wait(min(remaining / 1000.0, _POLL_SECONDS))
            return pending.decision or "deny", pending.resolved_reason or "unknown"

    # Called from the request-handling thread when an answer arrives.

    def resolve(self, approval_id: str, decision: Decision) -> Resolution:
        """Answer one approval: unknown ids are 404, expired ones 410, repeats are idempotent."""
        if decision not in ("allow", "deny"):
            from .errors import InvalidRequest

            raise InvalidRequest(f"decision 只能是 allow 或 deny，收到 {decision!r}")

        with self._condition:
            pending = self._pending.get(approval_id)
            if pending is not None:
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
        pending.resolved_at = events.now_ms()
        pending.resolved_reason = reason
        self._pending.pop(pending.id, None)
        self._decided[pending.id] = _Decided(
            decision=decision, reason=reason, expired=pending.expired
        )


__all__ = [
    "APPROVAL_TIMEOUT_SECONDS",
    "ApprovalTable",
    "PendingApproval",
    "Resolution",
]
