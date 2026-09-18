"""工作区端点：列出候选、登记新工作区。

新建会话前必须先选工作区，所以候选列表是界面首屏就需要的读；登记是唯一的写入口
（``avid workspace add`` 走同一个 svc 方法，两条路不会分叉）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from ..schemas import CreateWorkspaceIn, WorkspaceListOut, WorkspaceOut
from . import current_services

router = APIRouter()


@router.get("/workspaces", response_model=WorkspaceListOut)
def list_workspaces(request: Request) -> dict:
    return {"workspaces": current_services(request).workspaces.list()}


@router.post(
    "/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED
)
def create_workspace(request: Request, body: CreateWorkspaceIn) -> dict:
    workspace = current_services(request).workspaces.register(
        body.path, name=body.name, permission=body.permission
    )
    return current_services(request).workspaces.describe(workspace)
