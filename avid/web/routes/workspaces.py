"""Workspace endpoints: list the candidates, register one and remove one from the candidate list."""

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
    """Registers a workspace, reporting an existing one as a conflict carrying its id and name."""
    services = current_services(request)
    workspace = services.workspaces.require_new(
        body.path, name=body.name, permission=body.permission
    )
    return services.workspaces.describe(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(request: Request, workspace_id: str) -> Response:
    """Unregisters a workspace from the candidate list, leaving every session under it untouched."""
    current_services(request).workspaces.unregister(workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workspaces/pick", response_model=PickFolderOut)
def pick_folder(request: Request) -> dict:
    """Opens the host folder picker, which is the only way to obtain an absolute directory path."""
    return {"path": current_services(request).workspaces.pick()}
