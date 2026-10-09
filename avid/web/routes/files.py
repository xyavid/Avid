"""Workspace file browsing: list one directory and preview one file, both read-only and confined
to a registered workspace root.

Path and credential rules live in ``services/files.py``; the route only translates service errors.
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
    """Resolves an id or path to a registered workspace; unknown is a 404, never a cwd fallback."""
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
