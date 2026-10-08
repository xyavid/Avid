"""工作区文件浏览：列一层目录 / 读一个文件的预览（都只读，都限定在已登记的工作区内）。

工作区按 id 解析（与终端桥同一条规矩：只认登记过的根目录），路径与凭据的判据在
`services/files.py`——路由只把服务错误翻成协议。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request

from ...services import files
from ...services.workspaces import Workspace, WorkspaceMissing
from ..schemas import FileContentOut, FileListOut
from . import current_services

router = APIRouter()


def _workspace(request: Request, workspace_id: str) -> Workspace:
    """按 id（或路径）取一个已登记的工作区；未知一律 404，不做"回落到当前目录"的猜测。"""
    found = current_services(request).workspaces.find_known(workspace_id)
    if found is None:
        raise WorkspaceMissing(f"没有这个工作区：{workspace_id}")
    return found


@router.get("/workspaces/{workspace_id}/files", response_model=FileListOut)
def list_files(
    request: Request,
    workspace_id: str,
    path: str = Query("", description="相对工作区根的目录路径；空串 = 根"),
) -> dict:
    workspace = _workspace(request, workspace_id)
    return files.list_dir(Path(workspace.root), path)


@router.get("/workspaces/{workspace_id}/file", response_model=FileContentOut)
def read_file(request: Request, workspace_id: str, path: str = Query(..., min_length=1)) -> dict:
    workspace = _workspace(request, workspace_id)
    return files.read_text(Path(workspace.root), path)
