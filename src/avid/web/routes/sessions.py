"""会话端点：列表、新建、元信息、改名、销毁、条目分页。

分页纪律在服务端（不变量 I14）：默认 ``limit=100``、硬上限 500、游标
``cursor_seq`` 排他。``/api/sessions`` 的 O(会话数 × 文件大小) 代价是已知的，
触发条件写在与 ``cli.py`` 相同的注释里（会话名冗余进 JSONL header）。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status

from ..schemas import (
    CreateSessionIn,
    EntryPageOut,
    RenameSessionIn,
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
    return current_services(request).sessions.create(id=body.id, name=body.name)


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


@router.get("/sessions/{session_id}/entries", response_model=EntryPageOut)
def list_entries(
    request: Request,
    session_id: str,
    branch: str = Query(default="main"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int | None = Query(default=None, ge=1),
    cursor_seq: int | None = Query(default=None, ge=0),
) -> dict:
    return current_services(request).sessions.entries(
        session_id,
        branch=branch,
        order=order,
        limit=limit,
        cursor_seq=cursor_seq,
    )


__all__ = ["router"]
