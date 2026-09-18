"""审批端点：查待决列表、答复。

答复是命令流（POST 往返），不走事件流——审批有明确结果且必须幂等（§5.1）。
失败关闭：超时/取消/断连/重启四条路径都收敛到 deny，浏览器只是决策的输入端
（不变量 I6）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import AnswerApprovalIn, AnswerApprovalOut, ApprovalListOut
from . import current_services

router = APIRouter()


@router.get("/runs/{run_id}/approvals", response_model=ApprovalListOut)
def list_approvals(request: Request, run_id: str) -> dict:
    """当前待决项。刷新页面与第二个标签页靠它恢复（SSE 重放也会重发请求事件）。"""
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
    """答复一次审批。

    幂等：同一结论重复投递返回 ``200 {accepted:false, already}``，不二次批准
    （§5.4、B5）；换了结论才 409；过期 410；未知 404。

    这里**不发事件**：``approval_resolved`` 由运行线程在阻塞等待返回后统一发出，
    于是事件的 ``seq`` 仍由唯一发射线程分配（不变量 I4），HTTP 线程只置结论。
    """
    registry = current_services(request).runs
    record = registry.get(run_id)
    if record.approvals is None:
        from ...svc.errors import ApprovalNotFound

        raise ApprovalNotFound(f"运行没有待决审批：{run_id}")

    result = record.approvals.resolve(approval_id, body.decision)
    return {
        "accepted": result.accepted,
        "decision": result.decision,
        "already": None if result.accepted else result.decision,
        "reason": result.reason,
        "approval_id": approval_id,
    }


__all__ = ["router"]
