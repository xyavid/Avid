"""工作区服务：注册表的 HTTP 面，以及"按工作区取会话仓库"。

一个进程要能服务多个工作区：会话库不再是进程级的一个目录，而是每个工作区各自的
``<root>/.avid/sessions``。这里集中回答两个问题——**有哪些工作区**、**这个会话属于哪一个**。

两种装配模式，区别只在"没有指定工作区时怎么办"：

* **多工作区模式**（``avid web`` 的默认）：必须显式指定工作区，否则 ``WorkspaceRequired``；
* **单工作区模式**（``Services(root=...)`` 或 ``avid web --workspace X``）：那一个就是默认，
  省略时的归属仍被写进会话 header，因此不存在"归属不明的会话"。

仓库按工作区缓存：同一个工作区里的会话句柄独占由 ``JsonlSessionRepo`` 自己保证，
缓存只是让"运行中刷新页面"复用同一个仓库实例（阶段 15 的 I3 前提）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..session import JsonlSessionRepo, SessionMetadata
from ..workspaces import Workspace, WorkspaceError, WorkspaceRegistry
from .errors import ServiceError, SessionNotFound

logger = logging.getLogger("avid.svc.workspaces")


class WorkspaceRequired(ServiceError):
    """多工作区模式下没有指定工作区。"""

    code = "workspace_required"
    status = 400


class WorkspaceMissing(ServiceError):
    """指定的工作区不存在／未登记。"""

    code = "workspace_not_found"
    status = 404


class WorkspaceInvalid(ServiceError):
    """工作区参数本身不合法（目录不存在、不是目录、模式名不认识）。"""

    code = "workspace_invalid"
    status = 400


class WorkspaceService:
    def __init__(
        self,
        registry: WorkspaceRegistry,
        *,
        default: Workspace | None = None,
        default_sessions_root: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.default = default
        # 单工作区模式下调用方直接给会话库路径（测试与 `--workspace` 都不必
        # 先造出 `<root>/.avid/sessions` 这层目录）。
        self.default_sessions_root = (
            Path(default_sessions_root) if default_sessions_root is not None else None
        )
        self._repos: dict[str, JsonlSessionRepo] = {}

    # ---------------- 查询 ----------------

    def workspaces(self) -> list[Workspace]:
        """默认工作区排在最前，其余按最近使用。"""
        items = self.registry.list()
        if self.default is None:
            return items
        return [self.default, *(ws for ws in items if ws.id != self.default.id)]

    def list(self) -> list[dict[str, Any]]:
        return [self.describe(ws) for ws in self.workspaces()]

    def resolve(self, selection: str | None) -> Workspace:
        """把请求里的工作区选择解析成记录。``None`` 只在单工作区模式下合法。"""
        if selection:
            try:
                found = self.registry.find(selection)
            except WorkspaceError as exc:  # 注册表损坏：读已降级为空表
                raise WorkspaceInvalid(str(exc)) from exc
            if found is None:
                raise WorkspaceMissing(
                    f"没有这个工作区：{selection}（用 GET /api/workspaces 看可选值）"
                )
            return found
        if self.default is not None:
            return self.default
        raise WorkspaceRequired(
            "新建会话必须指定 workspace（多工作区模式下没有默认值）"
        )

    def register(
        self, path: str, *, name: str | None = None, permission: str | None = None
    ) -> Workspace:
        try:
            return self.registry.add(path, name=name, permission=permission)
        except WorkspaceError as exc:
            raise WorkspaceInvalid(str(exc)) from exc
        except ValueError as exc:  # 未知权限模式
            raise WorkspaceInvalid(str(exc)) from exc

    def describe(self, workspace: Workspace) -> dict[str, Any]:
        record = workspace.to_dict()
        record["is_default"] = self.default is not None and workspace.id == self.default.id
        return record

    # ---------------- 仓库 ----------------

    def sessions_root(self, workspace: Workspace) -> Path:
        if (
            self.default is not None
            and workspace.id == self.default.id
            and self.default_sessions_root is not None
        ):
            return self.default_sessions_root
        return self.registry.sessions_root(workspace)

    def repo_for(self, workspace: Workspace) -> JsonlSessionRepo:
        repo = self._repos.get(workspace.id)
        if repo is None:
            repo = JsonlSessionRepo(
                self.sessions_root(workspace), workspace=workspace.id
            )
            self._repos[workspace.id] = repo
        return repo

    def repo_of_session(self, session_id: str) -> tuple[Workspace, SessionMetadata]:
        """会话属于哪个工作区。会话 id 不携带工作区信息，所以只能逐个库找。"""
        for workspace in self.workspaces():
            found = self._find(self.repo_for(workspace), session_id)
            if found is not None:
                return workspace, found
        raise SessionNotFound(f"没有这个会话：{session_id}")

    def find_session(self, session_id: str) -> tuple[Workspace, SessionMetadata] | None:
        try:
            return self.repo_of_session(session_id)
        except SessionNotFound:
            return None

    @staticmethod
    def _find(repo: JsonlSessionRepo, session_id: str) -> SessionMetadata | None:
        for meta in repo.list():
            if meta.id == session_id:
                return meta
        return None

    def close(self) -> None:
        for repo in self._repos.values():
            repo.close()
        self._repos.clear()


def single_workspace(root: str | Path) -> Workspace:
    """单工作区模式的记录：``Services(root=...)`` 传的是**会话库路径**。

    兼容两种形状：``<工作区>/.avid/sessions``（正常装配）与任意目录（测试直接给一个
    临时目录当会话库）。id 仍由工作区根派生，于是它和注册表里的同一目录一致。
    """
    from ..workspaces import derive_id

    sessions = Path(root).resolve()
    parts = sessions.parts
    if len(parts) >= 3 and parts[-2:] == (".avid", "sessions"):
        base = sessions.parent.parent
    else:
        base = sessions
    return Workspace(
        id=derive_id(base),
        root=str(base),
        name=base.name or "workspace",
        created_at=0,
        last_used_at=0,
    )


__all__ = [
    "WorkspaceInvalid",
    "WorkspaceMissing",
    "WorkspaceRequired",
    "WorkspaceService",
    "single_workspace",
]
