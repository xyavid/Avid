"""Session endpoints: listing, creation, metadata, rename, deletion, branches and entry pages."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from ..schemas import (
    BranchListOut,
    BranchOut,
    CreateBranchIn,
    CreateSessionIn,
    EntryPageOut,
    RenameSessionIn,
    ScratchIn,
    ScratchOut,
    SessionDetail,
    SessionListOut,
)
from . import current_services

router = APIRouter()


@router.get("/sessions", response_model=SessionListOut)
def list_sessions(request: Request) -> dict:
    return {"sessions": current_services(request).sessions.list_sessions()}


@router.post("/sessions", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
def create_session(request: Request, body: CreateSessionIn) -> dict:
    return current_services(request).sessions.create(
        id=body.id, name=body.name, workspace=body.workspace
    )


@router.post(
    "/sessions/{session_id}/scratch",
    response_model=ScratchOut,
    status_code=status.HTTP_201_CREATED,
)
def create_scratch_session(request: Request, session_id: str, body: ScratchIn) -> dict:
    """Creates a scratch session: a copy of the source context, read-only and destroyed on close."""
    return current_services(request).sessions.create_scratch(session_id, name=body.name)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session(request: Request, session_id: str) -> dict:
    return current_services(request).sessions.get(session_id)


@router.patch("/sessions/{session_id}", response_model=SessionDetail)
def rename_session(request: Request, session_id: str, body: RenameSessionIn) -> dict:
    return current_services(request).sessions.rename(session_id, body.name)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(request: Request, session_id: str) -> Response:
    current_services(request).sessions.delete(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/branches", response_model=BranchListOut)
def list_branches(request: Request, session_id: str) -> dict:
    """Lists branches, where main is reported as the implicit default of a fresh session."""
    return current_services(request).sessions.list_branches(session_id)


@router.post(
    "/sessions/{session_id}/branches",
    response_model=BranchOut,
    status_code=status.HTTP_201_CREATED,
)
def create_branch(request: Request, session_id: str, body: CreateBranchIn) -> dict:
    """Forks a new branch at the given entry; an active run or a duplicate name is a conflict."""
    return current_services(request).sessions.create_branch(
        session_id, name=body.name, at=body.at
    )


@router.get("/sessions/{session_id}/entries", response_model=EntryPageOut)
def list_entries(
    request: Request,
    session_id: str,
    branch: str = Query(default="main"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1),
    cursor_seq: int | None = Query(default=None, ge=0),
) -> dict:
    """Pages one branch's entries with an exclusive cursor; the service caps the page size."""
    return current_services(request).sessions.entries(
        session_id,
        branch=branch,
        order=order,
        limit=limit,
        cursor_seq=cursor_seq,
    )


@router.get("/sessions/{session_id}/entries/{entry_id}/attachments/{index}")
def read_attachment(request: Request, session_id: str, entry_id: str, index: int) -> Response:
    """One image part's raw bytes; a committed entry never changes, so this caches long."""
    data, mime = current_services(request).sessions.attachment_bytes(session_id, entry_id, index)
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=3600"},
    )


__all__ = ["router"]
