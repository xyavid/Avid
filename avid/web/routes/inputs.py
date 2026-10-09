"""补充输入的端点（阶段 60）：投一条、看队列、撤销一条。

语义只有一句话：**最早可能被处理的时刻**。``mode=now`` 落在活动 run 的下一个 step，
空闲就直接起一个 run；``mode=after`` 等下一 turn。队列本身是进程内存态（见
`avid/services/inbox.py` 的三条不变量），落哪一条、什么时候落，由 run 线程说了算。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from ..schemas import InputAcceptedOut, InputIn, InputListOut
from . import current_services

router = APIRouter()


@router.post(
    "/sessions/{session_id}/inputs",
    response_model=InputAcceptedOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_input(request: Request, session_id: str, body: InputIn) -> dict:
    """收下一条输入；`kind=run` 表示这就起了一个 run（去接它的流），`input` 表示留在队里。"""
    return current_services(request).runs.submit_input(
        session_id,
        mode=body.mode,
        prompt=body.prompt,
        images=[image.model_dump() for image in body.images],
        model=body.model,
        effort=body.reasoning_effort,
        branch=body.branch,
        full_ack=body.full_access_ack,
        auto_approve=body.auto_approve,
        client_id=body.client_id,
    )


@router.get("/sessions/{session_id}/inputs", response_model=InputListOut)
def list_inputs(request: Request, session_id: str) -> dict:
    """这个会话还没被采纳的输入（刷新后据此把「排队中」的段落画回来）。"""
    return {"inputs": current_services(request).runs.list_inputs(session_id)}


@router.delete("/sessions/{session_id}/inputs/{input_id}", status_code=status.HTTP_204_NO_CONTENT)
def drop_input(request: Request, session_id: str, input_id: str) -> None:
    """撤销一条**尚未领取**的输入；已经领走的那条是会话里的事实，撤不动。"""
    current_services(request).runs.drop_input(session_id, input_id)


__all__ = ["router"]
