"""工作区端点：列出候选、登记新工作区、从候选里摘掉一个。

新建会话前必须先选工作区，所以候选列表是界面首屏就需要的读；登记与摘掉是仅有的两个
写入口（``avid workspace add`` / ``avid workspace remove`` 走同一对 svc 方法，
两条路不会分叉）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from ..schemas import (
    CreateWorkspaceIn,
    PickFolderOut,
    WorkspaceListOut,
    WorkspaceOut,
)
from . import current_services

router = APIRouter()


@router.get("/workspaces", response_model=WorkspaceListOut)
def list_workspaces(request: Request) -> dict:
    return {"workspaces": current_services(request).workspaces.list()}


@router.post(
    "/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED
)
def create_workspace(request: Request, body: CreateWorkspaceIn) -> dict:
    """登记一个工作区。已在列表里（含进程绑定的那个）→ 409 ``workspace_exists``，
    ``detail`` 带上已存在的 id/名字，界面据此直接切过去而不是报错卡住。"""
    services = current_services(request)
    workspace = services.workspaces.require_new(
        body.path, name=body.name, permission=body.permission
    )
    return services.workspaces.describe(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(request: Request, workspace_id: str) -> Response:
    """从候选列表里摘掉一个工作区（**不删会话数据**）。

    它下面的会话仍然列举得到、打得开，界面上归到「未归属的会话」——所以这个端点回 204
    而不是回一个"删掉了什么"的正文：动作的对象是**列表里的一项**，不是磁盘上的目录。
    想删的是进程绑定的那个工作地点 → 409 ``workspace_bound``（它永远在候选里）；
    重删一个已经摘掉的 → 幂等，同样 204。
    """
    current_services(request).workspaces.unregister(workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/pick", response_model=PickFolderOut)
def pick_folder(request: Request) -> dict:
    """弹一次**宿主机**的文件夹选择器（浏览器拿不到目录绝对路径，只能后端来）。

    用户取消返回 ``{"path": null}``；已有对话框开着 409；没有可用后端 503
    （消息里给出 `avid workspace add <路径>` 这条替代做法）。
    """
    return {"path": current_services(request).workspaces.pick()}
