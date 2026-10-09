"""Approval endpoints: list the pending items and answer one of them.

一张表两种待决：毁灭级裁决（decision: allow/deny）与模型的提问（answer: 文本）。
列表与答复都走同一对端点——界面只需要一个「等你说话」的槽。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import AnswerApprovalIn, AnswerApprovalOut, ApprovalListOut
from . import current_services

router = APIRouter()


@router.get("/runs/{run_id}/approvals", response_model=ApprovalListOut)
def list_approvals(request: Request, run_id: str) -> dict:
    """Lists the pending items, which is how a refreshed page or a second tab recovers them."""
    record = current_services(request).runs.get(run_id)
    pending = record.approvals.pending() if record.approvals is not None else []
    return {"approvals": [item.to_dict() for item in pending]}


@router.post(
    "/runs/{run_id}/approvals/{approval_id}",
    response_model=AnswerApprovalOut,
)
def answer_approval(
    request: Request, run_id: str, approval_id: str, body: AnswerApprovalIn
) -> dict:
    """Answers one approval idempotently, emitting no event because the run thread owns the sequence."""
    registry = current_services(request).runs
    record = registry.get(run_id)
    if record.approvals is None:
        from ...services.errors import ApprovalNotFound

        raise ApprovalNotFound(f"运行没有待决审批：{run_id}")

    if body.answer is not None:
        result = record.approvals.answer(approval_id, body.answer)
    elif body.decision is not None:
        result = record.approvals.resolve(approval_id, body.decision)
    else:
        from ...services.errors import InvalidRequest

        raise InvalidRequest("要回什么？裁决给 decision，提问给 answer")
    return {
        "accepted": result.accepted,
        "decision": result.decision,
        "already": None if result.accepted else result.decision,
        "reason": result.reason,
        "approval_id": approval_id,
    }


__all__ = ["router"]
