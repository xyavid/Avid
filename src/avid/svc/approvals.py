"""待决审批表：把阻塞式审批搬到网络边界（F1）。

三件事在这里收敛：

* **阻塞等待**：``request()`` 在运行线程里等答复，等到就返回 ``allow``/``deny``；
* **失败关闭**（不变量 I6）：超时、取消、run 结束、服务重启四条路径全部收敛到
  ``deny``——浏览器只是决策的**输入端**，未答复一律不是允许；
* **幂等**：已决表记住每个 ``approval_id`` 的结论，重复投递不再二次批准。

同一 run 内可能有多个待决审批：``subagent`` 最多 4 个并行（``tools/subagent.py``），
而工具在父 run 内顺序执行，所以父 run 自身至多一个、其余来自子 run。CLI 侧那条
模块级 ``_ASK_LOCK`` 只为 stdin 保留，Web 路径不经过它。
"""

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

# 超时默认 120s，且必须**小于** subagent 的批次预算 300s（tools/subagent.py:37），
# 否则子 agent 会先被整体超时掐掉，审批永远等不到答复（§5.4）。
APPROVAL_TIMEOUT_SECONDS = 120.0

# 等待粒度：用小步长轮询而不是一次等到超时，才能及时看见取消与关闭。
_POLL_SECONDS = 0.2

Decision = str  # "allow" | "deny"


@dataclass
class PendingApproval:
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
    """一次答复的结果。``accepted=False`` 表示没有二次批准。"""

    accepted: bool
    decision: Decision
    reason: str


@dataclass
class _Decided:
    decision: Decision
    reason: str
    expired: bool = False


@dataclass
class ApprovalTable:
    """一个 run 的审批表。

    ``emit`` / ``set_status`` / ``is_cancelled`` 由 ``svc/runs.py`` 注入：表自己不
    知道事件的 seq 怎么分配，也不认识运行记录（那是注册表的事）。
    """

    emit: Callable[..., object]
    set_status: Callable[[str], None]
    is_cancelled: Callable[[], bool]
    timeout: float = APPROVAL_TIMEOUT_SECONDS
    new_id: Callable[[], str] = field(
        default=lambda: f"ap_{uuid.uuid4().hex[:12]}"
    )
    _condition: threading.Condition = field(
        default_factory=threading.Condition, repr=False
    )
    _pending: dict[str, PendingApproval] = field(default_factory=dict, repr=False)
    _decided: dict[str, _Decided] = field(default_factory=dict, repr=False)
    _closed: bool = False

    # ---------------- 运行线程侧 ----------------

    def request(self, name: str, arguments: dict[str, Any], reason: str) -> bool:
        """``AskUser`` 签名：阻塞等答复，返回是否允许。"""
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
                    pending.expired = True
                    self._finish(pending, "deny", "timeout")
                    break
                self._condition.wait(min(remaining / 1000.0, _POLL_SECONDS))
            return pending.decision or "deny", pending.resolved_reason or "unknown"

    # ---------------- HTTP 线程侧 ----------------

    def resolve(self, approval_id: str, decision: Decision) -> Resolution:
        """答复一次审批。未知 → 404；过期 → 410；同值重复 → accepted:false。"""
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
            if previous.expired:
                raise ApprovalExpired(f"审批已过期：{approval_id}")
            if previous.decision != decision:
                # 已决且答复不同：409。同一答复重复投递是幂等，走下面那条。
                raise ApprovalConflict(
                    f"审批已经以 {previous.decision} 结束：{approval_id}"
                )
            return Resolution(
                accepted=False, decision=previous.decision, reason=previous.reason
            )

    def _closed_now(self) -> bool:
        """持锁后再查一次关闭标志。

        另一个线程可能在两次检查之间 ``close()``，所以这次重查是必要的；
        写成方法是为了绕开 mypy 的属性窄化（它假设属性两次读之间不变）。
        """
        return self._closed

    def pending(self) -> list[PendingApproval]:
        with self._condition:
            return list(self._pending.values())

    def close(self, reason: str = "run_ended") -> None:
        """run 结束（正常/失败/取消）时调用：未决审批一律收敛为拒绝。"""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    # ---------------- 内部 ----------------

    def _finish(self, pending: PendingApproval, decision: Decision, reason: str) -> None:
        """在持锁状态下结算一条审批。"""
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
