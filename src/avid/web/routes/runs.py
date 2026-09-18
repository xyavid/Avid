"""运行端点：起一次运行、查状态、请求取消。

``POST /sessions/{id}/runs`` 起工作线程后立刻返回 201 —— 一次运行是 30 秒到
数分钟，同步 HTTP 既会超时也无法增量观察（设计文档 §10 的异步化七问）。
取消是**显式命令**：关页面或刷新不算取消，因为 run 会持久化（§5.4）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from ..schemas import CancelOut, RunCreatedOut, RunOut, StartRunIn
from . import current_services

router = APIRouter()


@router.post(
    "/sessions/{session_id}/runs",
    response_model=RunCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def start_run(request: Request, session_id: str, body: StartRunIn) -> dict:
    """``branch`` 决定这次运行接在哪条链尾上（缺省 main）。"""
    record = current_services(request).runs.start(
        session_id, body.prompt, auto_approve=body.auto_approve, branch=body.branch
    )
    return {
        "run_id": record.run_id,
        "session_id": record.session_id,
        "status": record.status,
    }


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(request: Request, run_id: str) -> dict:
    return current_services(request).runs.get(run_id).to_dict()


@router.post("/runs/{run_id}/cancel", response_model=CancelOut, status_code=status.HTTP_202_ACCEPTED)
def cancel_run(request: Request, run_id: str) -> dict:
    """请求取消：**在下一个检查点生效**，不承诺立即停止（§7.4）。"""
    record = current_services(request).runs.cancel(run_id)
    return {
        "run_id": record.run_id,
        "status": record.status,
        "cancel_requested": record.cancel_requested,
    }


__all__ = ["router"]
