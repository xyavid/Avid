"""Workspace service: the registry's surface plus the per-workspace session repositories."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from ..session import JsonlSessionRepo, SessionMetadata
from ..workspaces import (
    Workspace,
    WorkspaceError,
    WorkspaceRegistry,
    derive_id,
    sessions_root,
)
from .errors import (
    PickerBusy,
    ServiceError,
    SessionNotFound,
    WorkspaceExists,
)
from .errors import (
    PickerFailed as PickerFailedError,
)
from .errors import (
    PickerUnavailable as PickerUnavailableError,
)
from .picker import PickerError, PickerFailed, PickerUnavailable, pick_directory

logger = logging.getLogger("avid.svc.workspaces")


class WorkspaceRequired(ServiceError):
    """No workspace was named although this request requires one."""

    code = "workspace_required"
    status = 400


class WorkspaceMissing(ServiceError):
    """The named workspace does not exist or is not registered."""

    code = "workspace_not_found"
    status = 404


class WorkspaceInvalid(ServiceError):
    """The workspace argument itself is invalid, such as a missing or non-directory path."""

    code = "workspace_invalid"
    status = 400


class WorkspaceBound(ServiceError):
    """Refusal to delete the process-bound working location, which is never a registry entry."""

    code = "workspace_bound"
    status = 409


# Lifetime of the session-ownership cache; it is short because the cache only saves a rescan.
SESSION_LOOKUP_TTL_SECONDS = 5.0


class WorkspaceService:
    """Workspace listing and resolution, plus cached repositories and session ownership lookups."""

    def __init__(
        self,
        registry: WorkspaceRegistry,
        *,
        default: Workspace | None = None,
        default_sessions_root: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.default = default
        # Set when the caller passes a session store path directly, as tests and root do.
        self.default_sessions_root = (
            Path(default_sessions_root) if default_sessions_root is not None else None
        )
        # One cached repository per workspace, so an active run and a page refresh share a handle.
        self._repos: dict[str, JsonlSessionRepo] = {}
        # Session id to (expiry, workspace, metadata); a hit avoids scanning every workspace.
        self._lookup: dict[str, tuple[float, Workspace, SessionMetadata]] = {}
        # Only one dialog may be open at a time, since a second window would hide the first.
        self._pick_lock = threading.Lock()

    # Workspace queries and resolution.

    def workspaces(self) -> list[Workspace]:
        """Selectable workspaces: the process-bound one first, then registry entries by use."""
        items = self.registry.list()
        if self.default is None:
            return items
        return [self.default, *(ws for ws in items if ws.id != self.default.id)]

    def known_workspaces(self) -> list[Workspace]:
        """Every workspace that can still hold sessions, removed ones included."""
        visible = self.workspaces()
        seen = {ws.id for ws in visible}
        hidden = [
            ws for ws in self.registry.list(include_hidden=True) if ws.id not in seen
        ]
        return [*visible, *hidden]

    def list(self) -> list[dict[str, Any]]:
        return [self.describe(ws) for ws in self.workspaces()]

    def resolve(self, selection: str | None) -> Workspace:
        """Resolve a request's workspace selection; omitting it is an error, never a default."""
        text = (selection or "").strip()
        if not text:
            raise WorkspaceRequired(
                "新建会话必须指定 workspace（进程的绑定工作区只是预选项，不是默认值）"
            )
        # The bound location may be unregistered, so match it explicitly by id or by path.
        for workspace in self.workspaces():
            if workspace.id == text or workspace.root == self._as_root(text):
                return workspace
        try:
            found = self.registry.find(text)
        except WorkspaceError as exc:  # a corrupt registry degrades to an empty list
            raise WorkspaceInvalid(str(exc)) from exc
        if found is None:
            raise WorkspaceMissing(
                f"没有这个工作区：{text}（用 GET /api/workspaces 看可选值）"
            )
        return found

    @staticmethod
    def _as_root(text: str) -> str:
        """Normalise a path for comparison, falling back to the raw text when it cannot resolve."""
        try:
            return str(Path(text).expanduser().resolve())
        except (OSError, RuntimeError):
            return text

    def find_known(self, selection: str) -> Workspace | None:
        """Look up a known workspace by id or path, the process-bound one included."""
        text = (selection or "").strip()
        if not text:
            return None
        resolved = self._as_root(text)
        for workspace in self.workspaces():
            if workspace.id == text or workspace.root == resolved:
                return workspace
        return None

    def register(
        self, path: str, *, name: str | None = None, permission: str | None = None
    ) -> tuple[Workspace, bool]:
        """Register a workspace and report whether it was newly created."""
        existing = self.find_known(path)
        if existing is not None:
            # Already listed, bound or registered: never add twice and never silently rename it.
            return existing, False
        try:
            created = self.registry.add(path, name=name, permission=permission)
        except WorkspaceError as exc:
            raise WorkspaceInvalid(str(exc)) from exc
        except ValueError as exc:  # unknown permission mode
            raise WorkspaceInvalid(str(exc)) from exc
        return created, existing is None

    def require_new(
        self,
        path: str,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> Workspace:
        """Strict registration that fails with the existing record when the workspace is known."""
        # Name and permission are applied in one write, so an invalid mode leaves nothing behind.
        workspace, created = self.register(path, name=name, permission=permission)
        if not created:
            raise WorkspaceExists(
                f"这个文件夹已经在工作区列表里：{workspace.name}（{workspace.root}）",
                detail={
                    "id": workspace.id,
                    "name": workspace.name,
                    "root": workspace.root,
                },
            )
        return workspace

    def unregister(self, selection: str) -> Workspace:
        """Drop a workspace from the selectable list without touching its session data."""
        workspace = self.find_known(selection)
        if workspace is None:
            # Not selectable: it may be a tombstone, and looking it up gives an accurate error.
            try:
                workspace = self.registry.get(selection)
            except WorkspaceError as exc:
                raise WorkspaceMissing(
                    f"没有这个工作区：{selection}（用 GET /api/workspaces 看可选值）"
                ) from exc
        if self.default is not None and workspace.id == self.default.id:
            raise WorkspaceBound(
                f"「{workspace.name}」是进程绑定的工作地点（{workspace.root}），"
                "它永远在候选列表里，不能从列表里删掉"
            )
        if workspace.hidden:
            # Already a tombstone: deleting again succeeds without writing to disk a second time.
            return workspace
        try:
            return self.registry.remove(workspace.id)
        except WorkspaceError as exc:
            raise WorkspaceInvalid(str(exc)) from exc

    # Host folder picker.

    def pick(self) -> str | None:
        """Open the host folder picker and return an absolute path, or None when cancelled."""
        # A second dialog would cover the first, so a busy picker is reported instead of waiting.
        if not self._pick_lock.acquire(blocking=False):
            raise PickerBusy("已经有一个文件夹选择器开着了；先去那边选完或取消")
        try:
            chosen = pick_directory()
        except PickerUnavailable as exc:
            raise PickerUnavailableError(str(exc)) from exc
        except PickerFailed as exc:
            raise PickerFailedError(str(exc)) from exc
        except PickerError as exc:  # fallback for a subclass missed above
            raise PickerFailedError(str(exc)) from exc
        finally:
            self._pick_lock.release()

        if chosen is None:
            return None
        path = Path(chosen).expanduser()
        if not path.is_dir():
            raise WorkspaceInvalid(f"选中的路径不存在或不是目录：{chosen}")
        return str(path.resolve())

    def describe(self, workspace: Workspace) -> dict[str, Any]:
        record = workspace.to_dict()
        # The hidden flag is internal registry state, so it never reaches the wire format.
        record.pop("hidden", None)
        record["is_default"] = self.default is not None and workspace.id == self.default.id
        return record

    # Repositories and session ownership.

    def sessions_root(self, workspace: Workspace) -> Path:
        if (
            self.default is not None
            and workspace.id == self.default.id
            and self.default_sessions_root is not None
        ):
            return self.default_sessions_root
        return sessions_root(workspace)

    def repo_for(self, workspace: Workspace) -> JsonlSessionRepo:
        repo = self._repos.get(workspace.id)
        if repo is None:
            repo = JsonlSessionRepo(
                self.sessions_root(workspace), workspace=workspace.id
            )
            self._repos[workspace.id] = repo
        return repo

    def repo_of_session(self, session_id: str) -> tuple[Workspace, SessionMetadata]:
        """Find which workspace owns a session, scanning every known repository on a cache miss."""
        cached = self._lookup.get(session_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1], cached[2]

        expires = time.monotonic() + SESSION_LOOKUP_TTL_SECONDS
        for workspace in self.known_workspaces():
            for meta in self.repo_for(workspace).list():
                self._lookup[meta.id] = (expires, workspace, meta)

        cached = self._lookup.get(session_id)
        if cached is not None:
            return cached[1], cached[2]
        raise SessionNotFound(f"没有这个会话：{session_id}")

    def remember_session(self, workspace: Workspace, metadata: SessionMetadata) -> None:
        """Record ownership right after creation, so the next lookup needs no scan."""
        self._lookup[metadata.id] = (
            time.monotonic() + SESSION_LOOKUP_TTL_SECONDS,
            workspace,
            metadata,
        )

    def forget_session(self, session_id: str) -> None:
        """Drop a cached ownership entry so the next lookup rescans after a delete or rename."""
        self._lookup.pop(session_id, None)

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
        self._lookup.clear()


def bound_workspace(root: str | Path) -> Workspace:
    """A process-bound working location that is not registered; the directory must already exist."""
    path = Path(root).expanduser()
    if not path.exists():
        raise WorkspaceInvalid(f"工作区目录不存在：{path}")
    if not path.is_dir():
        raise WorkspaceInvalid(f"不是目录：{path}")
    resolved = path.resolve()
    return Workspace(
        id=derive_id(resolved),
        root=str(resolved),
        name=resolved.name or "workspace",
        created_at=0,
        last_used_at=0,
    )


def single_workspace(root: str | Path) -> Workspace:
    """Workspace derived from a session store path, either the standard layout or a plain one."""
    from ..workspaces import derive_id

    sessions = Path(root).resolve()
    parts = sessions.parts
    # The standard layout ends in .avid/sessions; otherwise the path is the workspace root itself.
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
    "bound_workspace",
    "WorkspaceInvalid",
    "WorkspaceMissing",
    "WorkspaceRequired",
    "WorkspaceService",
    "single_workspace",
]
