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

from ..agent.events import now_ms
from ..security import userdirs

logger = logging.getLogger("avid.services.workspace_registry")

# Re-exported for existing callers; policy/userdirs.py is the single definition of the Avid home dir.
AVID_HOME_ENV = userdirs.AVID_HOME_ENV
REGISTRY_FILE = "workspaces.json"
# Stamped into every file so the on-disk shape is self-describing.
REGISTRY_VERSION = 1
# Session stores live in one shared directory, one subdirectory per workspace id.
# The id is a digest of the resolved path, so a store survives a renamed or unregistered
# workspace; losing the registry loses the names, never the sessions.

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
    """A registered directory with its display name and timestamps; the registry is an index, not
    an authority, so permissions come only from each run's explicit arguments (a legacy
    default_permission field is ignored on read and disappears on the next write).
    """

    id: str
    root: str
    name: str
    created_at: int
    last_used_at: int
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
            "hidden": self.hidden,
        }


def _parse(raw: Any) -> Workspace | None:
    """Rebuilds one registry entry, defaulting missing or unknown fields instead of failing."""
    if not isinstance(raw, dict):
        return None
    root = raw.get("root")
    if not isinstance(root, str) or not root.strip():
        return None
    created = raw.get("created_at")
    used = raw.get("last_used_at")
    return Workspace(
        id=str(raw.get("id") or derive_id(root)),
        root=str(Path(root).expanduser().resolve()),
        name=str(raw.get("name") or Path(root).name or _UNNAMED),
        created_at=int(created) if isinstance(created, int) else now_ms(),
        last_used_at=int(used) if isinstance(used, int) else now_ms(),
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
    ) -> Workspace:
        """Registers a directory, updating the existing entry when the derived id is already known."""
        path = Path(root).expanduser()
        if not path.exists():
            raise WorkspaceError(f"目录不存在：{path}")
        if not path.is_dir():
            raise WorkspaceError(f"不是目录：{path}")
        resolved = path.resolve()

        # Read through the raising path: never overwrite a file that may still hold good data.
        items = self._read()
        stamp = self._now()
        for index, ws in enumerate(items):
            if ws.id == derive_id(resolved):
                updated = replace(
                    ws,
                    name=name or ws.name,
                    last_used_at=stamp,
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
        )
        self._write([*items, created])
        return created

    def remove(self, selection: str) -> Workspace:
        """Hides a workspace from candidate lists, keeping the entry and the sessions under its root."""
        found = self.get(selection)
        items = self._read()
        for index, ws in enumerate(items):
            if ws.id == found.id:
                items[index] = replace(ws, hidden=True)
        self._write(items)
        return replace(found, hidden=True)


def sessions_root(workspace: Workspace | str) -> Path:
    """The workspace's store inside the shared session directory, keyed by its stable id.

    A plain path is accepted too and resolves to the same place as the registered workspace
    would: the id is derived from the resolved path, not from the registry entry.
    """
    if isinstance(workspace, Workspace):
        return userdirs.sessions_dir() / workspace.id
    return userdirs.sessions_dir() / derive_id(workspace)
