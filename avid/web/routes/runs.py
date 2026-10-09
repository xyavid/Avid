"""Run endpoints: start a run, read its status and request cancellation."""

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
    """Starts a run on the chosen branch and returns before the work finishes.

    `from_input` 领取一条排队输入：内容与开关取自那条输入，客户端的 prompt/images 被忽略。
    """
    services = current_services(request)
    if body.from_input is not None:
        record = services.runs.start_queued(session_id, body.from_input)
        return {
            "run_id": record.run_id,
            "session_id": record.session_id,
            "status": record.status,
        }
    record = services.runs.start(
        session_id,
        body.prompt,
        images=[image.model_dump() for image in body.images],
        auto_approve=body.auto_approve,
        branch=body.branch,
        full_ack=body.full_access_ack,
        model=body.model,
        effort=body.reasoning_effort,
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
    """Requests cancellation, which takes effect at the next checkpoint rather than immediately."""
    record = current_services(request).runs.cancel(run_id)
    return {
        "run_id": record.run_id,
        "status": record.status,
        "cancel_requested": record.cancel_requested,
    }


__all__ = ["router"]
