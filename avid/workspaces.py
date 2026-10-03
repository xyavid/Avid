"""User-level registry of the workspace directories that are selectable on this machine."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .policy import userdirs
from .policy.permission import (
    DEFAULT_MODE,
    FullAccessError,
    full_grant_error,
    migrate_mode,
    validate_mode,
)
from .runtime.events import now_ms

logger = logging.getLogger("avid.workspaces")

# Re-exported for existing callers; policy/userdirs.py is the single definition of the Avid home dir.
AVID_HOME_ENV = userdirs.AVID_HOME_ENV
REGISTRY_FILE = "workspaces.json"
# Stamped into every file so the on-disk shape is self-describing.
REGISTRY_VERSION = 1
# Session stores live under each workspace root, so losing the registry loses no session.
SESSION_DIR = ".avid/sessions"

# Fallback display name for a root that has no directory name of its own (the filesystem root).
_UNNAMED = "workspace"


class WorkspaceError(Exception):
    """A workspace operation failed; the message is written for the user to read directly."""


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceRegistryCorrupt(WorkspaceError):
    pass


def home_dir() -> Path:
    """The user-level Avid directory, delegated to policy/userdirs.py where it is defined."""
    return userdirs.avid_home()


def registry_path() -> Path:
    return home_dir() / REGISTRY_FILE


def derive_id(root: str | Path) -> str:
    """Derives a stable id from the resolved absolute path, so one directory always has one id."""
    resolved = str(Path(root).expanduser().resolve())
    digest = hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:12]
    return f"w-{digest}"


@dataclass(frozen=True)
class Workspace:
    """A registered directory with its display name, timestamps and default permission mode."""

    id: str
    root: str
    name: str
    created_at: int
    last_used_at: int
    default_permission: str = DEFAULT_MODE
    # Hidden entries are tombstones, never deletions: the id is a path digest, so the entry is the
    # only record tying the sessions under that path to a workspace.
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "root": self.root,
            "name": self.name,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "default_permission": self.default_permission,
            "hidden": self.hidden,
        }


def _default_mode(permission: str) -> str:
    """Validates a workspace default, refusing full because a persisted grant would be silent."""
    problem = full_grant_error(permission, source="workspace_default")
    if problem is not None:
        raise WorkspaceError(problem)
    return validate_mode(permission)


def _parse(raw: Any) -> Workspace | None:
    """Rebuilds one registry entry, defaulting missing or unknown fields instead of failing."""
    if not isinstance(raw, dict):
        return None
    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        return None
    created = raw.get("created_at")
    used = raw.get("last_used_at")
    mode = raw.get("default_permission", DEFAULT_MODE)
    try:
        permission, note = migrate_mode(mode)
        if note:
            # Report an upgraded security setting instead of rewriting it silently.
            logger.warning("工作区 %s：%s", root, note)
    except (ValueError, FullAccessError):
        logger.warning("工作区 %s 的默认权限 %r 不认识，按默认处理", root, mode)
        permission = DEFAULT_MODE
    return Workspace(
        id=str(raw.get("id") or derive_id(root)),
        root=str(Path(root).expanduser().resolve()),
        name=str(raw.get("name") or Path(root).name or _UNNAMED),
        created_at=int(created) if isinstance(created, int) else now_ms(),
        last_used_at=int(used) if isinstance(used, int) else now_ms(),
        default_permission=permission,
        # A missing field means never removed, and a malformed value is read the same way.
        hidden=raw.get("hidden") is True,
    )


class WorkspaceRegistry:
    """Index of known workspaces; reads tolerate a corrupt file while writes refuse to overwrite it."""

    def __init__(self, path: str | Path | None = None, *, now: Callable[[], int] = now_ms):
        self.path = Path(path) if path is not None else registry_path()
        # Injectable clock so tests can pin created_at and last_used_at.
        self._now = now

    def _read(self) -> list[Workspace]:
        """Reads every entry, raising rather than silently discarding a corrupt file."""
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceRegistryCorrupt(
                f"工作区注册表读不了：{self.path}（{exc}）。"
                "修好这个文件，或删掉它再重新登记工作区。"
            ) from exc
        if not isinstance(raw, dict):
            raise WorkspaceRegistryCorrupt(
                f"工作区注册表格式不对：{self.path}。删掉它再重新登记工作区。"
            )
        items = raw.get("workspaces")
        if not isinstance(items, list):
            return []
        return [ws for ws in (_parse(item) for item in items) if ws is not None]

    def _write(self, workspaces: Iterable[Workspace]) -> None:
        payload = {
            "version": REGISTRY_VERSION,
            "workspaces": [ws.to_dict() for ws in workspaces],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write a sibling temp file and replace it, so an interrupted write cannot truncate the registry.
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temp, self.path)

    def list(self, *, include_hidden: bool = False) -> list[Workspace]:
        """Lists most-recently-used first, logging and returning empty when the file is corrupt."""
        try:
            items = self._read()
        except WorkspaceRegistryCorrupt as exc:
            logger.warning("%s", exc)
            return []
        # Listing sessions needs the tombstones too, or removing a workspace would hide its sessions.
        if not include_hidden:
            items = [ws for ws in items if not ws.hidden]
        return sorted(items, key=lambda ws: (-ws.last_used_at, ws.id))

    def find(self, selection: str | None, *, include_hidden: bool = False) -> Workspace | None:
        """Finds by id first and by resolved path second; tombstones are invisible unless asked for."""
        if not selection:
            return None
        text = str(selection).strip()
        if not text:
            return None
        try:
            items = self._read()
        except WorkspaceRegistryCorrupt as exc:
            logger.warning("%s", exc)
            items = []
        if not include_hidden:
            items = [ws for ws in items if not ws.hidden]

        for ws in items:
            if ws.id == text:
                return ws

        candidate = str(Path(text).expanduser().resolve())
        for ws in items:
            if ws.root == candidate:
                return ws
        return None

    def get(self, selection: str) -> Workspace:
        """Returns one record including tombstones, so write paths can target a removed workspace."""
        found = self.find(selection, include_hidden=True)
        if found is None:
            raise WorkspaceNotFound(
                f"没有这个工作区：{selection}。用 `avid workspace list` 看已登记的工作区，"
                "或 `avid workspace add <路径>` 登记一个。"
            )
        return found

    def add(
        self,
        root: str | Path,
        *,
        name: str | None = None,
        permission: str | None = None,
    ) -> Workspace:
        """Registers a directory, updating the existing entry when the derived id is already known."""
        path = Path(root).expanduser()
        if not path.exists():
            raise WorkspaceError(f"目录不存在：{path}")
        if not path.is_dir():
            raise WorkspaceError(f"不是目录：{path}")
        resolved = path.resolve()
        mode = _default_mode(permission or DEFAULT_MODE)

        # Read through the raising path: never overwrite a file that may still hold good data.
        items = self._read()
        stamp = self._now()
        for index, ws in enumerate(items):
            if ws.id == derive_id(resolved):
                updated = replace(
                    ws,
                    name=name or ws.name,
                    last_used_at=stamp,
                    default_permission=(
                        _default_mode(permission) if permission else ws.default_permission
                    ),
                    # Re-registering the same directory undoes a removal by reusing the tombstone.
                    hidden=False,
                )
                items[index] = updated
                self._write(items)
                return updated

        created = Workspace(
            id=derive_id(resolved),
            root=str(resolved),
            name=name or resolved.name or _UNNAMED,
            created_at=stamp,
            last_used_at=stamp,
            default_permission=mode,
        )
        self._write([*items, created])
        return created

    def set_permission(self, selection: str, permission: str) -> Workspace:
        mode = _default_mode(permission)
        found = self.get(selection)
        updated = self._update(found.id, default_permission=mode)
        # get() proved the record exists, so the update cannot miss it.
        assert updated is not None
        return updated

    def remove(self, selection: str) -> Workspace:
        """Hides a workspace from candidate lists, keeping the entry and the sessions under its root."""
        found = self.get(selection)
        items = self._read()
        for index, ws in enumerate(items):
            if ws.id == found.id:
                items[index] = replace(ws, hidden=True)
        self._write(items)
        return replace(found, hidden=True)

    def _update(self, workspace_id: str, **changes: Any) -> Workspace | None:
        items = self._read()
        for index, ws in enumerate(items):
            if ws.id == workspace_id:
                items[index] = replace(ws, **changes)
                self._write(items)
                return items[index]
        return None


def sessions_root(workspace: Workspace | str) -> Path:
    """Returns the session store of a workspace, which always sits under its own root."""
    root = workspace.root if isinstance(workspace, Workspace) else str(workspace)
    return Path(root) / SESSION_DIR
