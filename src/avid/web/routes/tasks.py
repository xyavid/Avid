"""任务端点：只读。

没有写端点不是遗漏：任务的写入者有且只有 ``TaskStore``（agent 的六个任务工具），
人类要改状态就走 ``/api/runs`` 让 agent 去调工具，权限闸门与审计因此不被绕过
（设计文档 §5.5/§6.1）。

任务库是**每个工作区一份**（``<工作区根>/.tasks/``），所以 ``?workspace=``
决定看哪个工作区；缺省是进程绑定的那个。
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..schemas import TaskListOut, TaskOut
from . import current_services

router = APIRouter()


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    request: Request, workspace: str | None = Query(default=None)
) -> dict:
    return {"tasks": current_services(request).tasks.list_tasks(workspace)}


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    request: Request, task_id: str, workspace: str | None = Query(default=None)
) -> dict:
    return current_services(request).tasks.get_task(task_id, workspace)


__all__ = ["router"]
