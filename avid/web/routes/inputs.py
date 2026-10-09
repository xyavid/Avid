"""Supplemental-input endpoints: submit one, list the queue, drop one.

The only semantics is the earliest moment an input may be handled; the queue itself is process
memory owned by the run thread (invariants in `avid/services/inbox.py`).
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
    """Accept one input; ``kind`` reports whether a run started (``run``) or it is queued."""
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
    """Inputs of this session not yet taken up; a refresh redraws queued segments from them."""
    return {"inputs": current_services(request).runs.list_inputs(session_id)}


@router.delete("/sessions/{session_id}/inputs/{input_id}", status_code=status.HTTP_204_NO_CONTENT)
def drop_input(request: Request, session_id: str, input_id: str) -> None:
    """Drop an input not yet claimed; a claimed one is a fact of the session and stays."""
    current_services(request).runs.drop_input(session_id, input_id)


__all__ = ["router"]
