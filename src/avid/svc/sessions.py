"""Read views over sessions for the application services, plus the rename and delete writes."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..session import (
    DEFAULT_BRANCH,
    USAGE_NS,
    BranchScan,
    SessionBranchExistsError,
    SessionError,
    SessionExistsError,
    SessionInvalidIdError,
    SessionMetadata,
    SessionUnknownTargetError,
)
from .errors import (
    BranchExists,
    InvalidRequest,
    SessionBusy,
    SessionExists,
    SessionNotFound,
    SessionReadError,
)
from .runs import RunRegistry
from .workspaces import Workspace, WorkspaceService

logger = logging.getLogger("avid.svc.sessions")

# Default and hard ceiling for one page of entries, since the server owns pagination discipline.
DEFAULT_ENTRY_LIMIT = 100
MAX_ENTRY_LIMIT = 500  # largest page the server will serve

# Entries the tail scan may inspect; beyond that it gives up instead of replaying the whole history.
_TAIL_SCAN_LIMIT = 64


class SessionService:
    """Session listing, lookup, branches and entries, sharing the run registry for handles."""

    def __init__(self, workspaces: WorkspaceService, runs: RunRegistry) -> None:
        self.workspaces = workspaces
        self.runs = runs

    # Listing and metadata reads.

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summaries of every session in every known workspace, including removed workspaces."""
        found: list[dict[str, Any]] = []
        for workspace in self.workspaces.known_workspaces():
            for meta in self.workspaces.repo_for(workspace).list():
                summary = self._summary(meta, workspace)
                if summary is not None:
                    found.append(summary)
        return found

    def _summary(
        self, meta: SessionMetadata, workspace: Workspace | None = None
    ) -> dict[str, Any] | None:
        try:
            name, count, truncated = self._facts(meta, workspace)
            return {
                "id": meta.id,
                "name": name,
                "created_at": meta.created_at,
                "storage_version": meta.storage_version,
                "parent_session_id": meta.parent_session_id,
                "workspace": self._workspace_field(workspace, meta),
                "message_count": count,
                "active_run_id": self.runs.active_run_id(meta.id),
                "truncated_tail": truncated,
            }
        except (SessionReadError, SessionError) as exc:
            # One unreadable session must not fail the whole listing, matching the CLI behaviour.
            logger.warning("跳过读不了的会话 %s：%s", meta.id, exc)
            return None

    def _facts(
        self, meta: SessionMetadata, workspace: Workspace | None
    ) -> tuple[str | None, int, bool]:
        """Name, count and tail flag from the cheap tail window, or by replay if undecidable."""
        if workspace is None:
            return self._facts_by_replay(meta, workspace)
        repo = self.workspaces.repo_for(workspace)
        summary = repo.summarize(meta)
        if summary.truncated_tail is None:
            return self._facts_by_replay(meta, workspace)
        return summary.name, summary.message_count, summary.truncated_tail

    def _facts_by_replay(
        self, meta: SessionMetadata, workspace: Workspace | None
    ) -> tuple[str | None, int, bool]:
        with self._session(meta.id, meta=meta, workspace=workspace) as session:
            return (
                session.get_name(),
                session.get_stats().message_count,
                self._truncated_tail(session),
            )

    def _workspace_field(
        self, workspace: Workspace | None, meta: SessionMetadata
    ) -> dict[str, Any]:
        """Wire shape of a session's ownership: id from its header, names from the registry."""
        owner = workspace
        if owner is None:
            found = self.workspaces.find_session(meta.id)
            owner = found[0] if found is not None else None
        workspace_id = meta.workspace or (owner.id if owner is not None else None)
        return {
            "id": workspace_id,
            "root": owner.root if owner is not None else None,
            "name": owner.name if owner is not None else None,
            # The UI preselects this mode because a session inherits its workspace default.
            "default_permission": (
                owner.default_permission if owner is not None else None
            ),
        }

    def get(self, session_id: str) -> dict[str, Any]:
        with self._session(session_id) as session:
            meta = session.metadata
            return {
                "id": meta.id,
                "name": session.get_name(),
                "created_at": meta.created_at,
                "storage_version": meta.storage_version,
                "parent_session_id": meta.parent_session_id,
                "workspace": self._workspace_field(None, meta),
                "message_count": session.get_stats().message_count,
                "active_run_id": self.runs.active_run_id(meta.id),
                "truncated_tail": self._truncated_tail(session),
                "branch": DEFAULT_BRANCH,
            }

    # Write paths: rename and delete.

    def create(
        self,
        *,
        workspace: str | None,
        id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Create a session; a workspace is mandatory, since ownership is an immutable fact."""
        owner = self.workspaces.resolve(workspace)
        repo = self.workspaces.repo_for(owner)
        try:
            session = repo.create(id=id, workspace=owner.id)
            self.workspaces.remember_session(owner, session.metadata)
        except SessionExistsError as exc:
            raise SessionExists(f"会话已存在：{id}") from exc
        except SessionInvalidIdError as exc:
            raise SessionReadError(f"会话 id 非法：{exc}") from exc
        try:
            if name is not None:
                session.set_name(name)
            return {
                "id": session.metadata.id,
                "name": session.get_name(),
                "created_at": session.metadata.created_at,
                "storage_version": session.metadata.storage_version,
                "parent_session_id": session.metadata.parent_session_id,
                "workspace": self.workspaces.describe(owner),
                "message_count": 0,
                "active_run_id": None,
                "truncated_tail": False,
                "branch": DEFAULT_BRANCH,
            }
        finally:
            session.close()

    def rename(self, session_id: str, name: str) -> dict[str, Any]:
        """Rename a session and return its refreshed view."""
        with self._session(session_id) as session:
            session.set_name(name)
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        # Deletion takes the session handle lock first, because destroying it needs the file closed.
        with self.runs.session_lock(session_id):
            if self.runs.active_run_id(session_id) is not None:
                raise SessionBusy(f"会话有活动 run，不能销毁：{session_id}")
            found = self.workspaces.find_session(session_id)
            if found is None:
                raise SessionNotFound(f"没有这个会话：{session_id}")
            workspace, metadata = found
            try:
                self.workspaces.repo_for(workspace).delete(metadata)
            except SessionError as exc:
                raise SessionReadError(f"销毁会话失败：{exc}") from exc
            self.workspaces.forget_session(session_id)

    # Branches.

    def list_branches(self, session_id: str) -> dict[str, Any]:
        """Branches with tip, entry count and the usage snapshot recorded on each branch."""
        with self._session(session_id) as session:
            usage_by_branch = {
                item.key: item.value for item in session.scan_values(USAGE_NS)
            }
            branches = [
                self._branch_to_dict(session, name, usage_by_branch.get(name))
                for name in session.branch_names()
            ]
        return {"session_id": session_id, "branches": branches}

    def create_branch(
        self, session_id: str, *, name: str | None = None, at: str | None = None
    ) -> dict[str, Any]:
        """Fork a new branch at an entry; refused while a run is still appending to the tip."""
        if self.runs.active_run_id(session_id) is not None:
            raise SessionBusy(f"会话有活动 run，不能分叉：{session_id}")
        with self._session(session_id) as session:
            chosen = name or self._next_branch_name(session)
            try:
                session.create_branch(chosen, at)
            except SessionBranchExistsError as exc:
                raise BranchExists(f"分支已存在：{chosen}") from exc
            except SessionUnknownTargetError as exc:
                raise InvalidRequest(f"分叉点不存在：{at}") from exc
            return self._branch_to_dict(session, chosen)

    @staticmethod
    def _branch_to_dict(
        session: Any, name: str, usage: Any = None
    ) -> dict[str, Any]:
        target = session.branch(name)
        return {
            "name": name,
            "tip_entry_id": None if target is None else target.get_tip_id(),
            "entry_count": 0 if target is None else len(target.find_entries(BranchScan())),
            "is_default": name == DEFAULT_BRANCH,
            # None means the branch has not run yet, so the UI shows a dash instead of guessing.
            "usage": usage,
        }

    @staticmethod
    def _next_branch_name(session: Any) -> str:
        """Next free auto-generated branch name, skipping names that already exist."""
        existing = set(session.branch_names())
        index = 2
        while f"b{index}" in existing:
            index += 1
        return f"b{index}"

    # Entry pagination.

    def entries(
        self,
        session_id: str,
        *,
        branch: str = DEFAULT_BRANCH,
        order: str = "desc",
        limit: int | None = None,
        cursor_seq: int | None = None,
    ) -> dict[str, Any]:
        """One page of entries, newest first by default because the first screen shows the tip."""
        if order not in ("asc", "desc"):
            raise InvalidRequest(f"order 只能是 asc 或 desc，收到 {order!r}")

        size = DEFAULT_ENTRY_LIMIT if limit is None else max(1, min(limit, MAX_ENTRY_LIMIT))
        with self._session(session_id) as session:
            target = session.branch(branch)
            entries = (
                []
                if target is None
                else target.find_entries(
                    BranchScan(
                        order="oldestFirst" if order == "asc" else "newestFirst",
                        limit=size + 1,  # one extra entry reveals whether another page exists
                        cursor_seq=cursor_seq,
                    )
                )
            )
            has_more = len(entries) > size
            page = entries[:size]
            next_cursor = page[-1].seq if has_more and page else None
            return {
                "session_id": session_id,
                "branch": branch,
                "order": order,
                "limit": size,
                "entries": [self._entry_to_dict(entry) for entry in page],
                "has_more": has_more,
                "next_cursor": next_cursor,
                # Only the tip page can be truncated: projection drops an unfinished batch.
                "truncated_tail": (
                    self._truncated_tail(session) if cursor_seq is None else False
                ),
            }

    @staticmethod
    def _entry_to_dict(entry: Any) -> dict[str, Any]:
        return {
            "entry_id": entry.id,
            "parent_id": entry.parent_id,
            "seq": entry.seq,
            "timestamp": entry.timestamp,
            "type": entry.type,
            "message": entry.message,
        }

    # Internals.

    def _truncated_tail(self, session: Any) -> bool:
        """Whether the tip holds tool calls without results, meaning the run stopped mid batch."""
        target = session.branch(DEFAULT_BRANCH)
        if target is None:
            return False
        entries = target.find_entries(BranchScan(order="newestFirst", limit=_TAIL_SCAN_LIMIT))
        # Tool result ids seen while walking back from the tip.
        seen: set[str] = set()
        for entry in entries:
            message = entry.message or {}
            if message.get("role") == "tool":
                seen.add(str(message.get("tool_call_id")))
                continue
            # The first non-result message ends the batch under inspection.
            expected = {
                str(call.get("id")) for call in (message.get("tool_calls") or [])
            }
            return bool(expected - seen)
        return False

    @contextmanager
    def _session(
        self,
        session_id: str,
        *,
        meta: SessionMetadata | None = None,
        workspace: Workspace | None = None,
    ) -> Iterator[Any]:
        """Yield a session handle: the one an active run holds, or one opened and closed here."""
        # Holding the handle lock keeps a read from colliding with a run opening the same session.
        with self.runs.session_lock(session_id):
            active = self.runs.active_session(session_id)
            if active is not None:
                yield active
                return

            owner = workspace
            metadata = meta
            # Callers that already know the owner skip the reverse lookup, which would be quadratic.
            if owner is None or metadata is None:
                found = self.workspaces.find_session(session_id)
                if found is None:
                    raise SessionNotFound(f"没有这个会话：{session_id}")
                owner, metadata = found
            try:
                session = self.workspaces.repo_for(owner).open(metadata)
            except SessionError as exc:
                raise SessionReadError(f"打不开会话 {session_id}：{exc}") from exc
            try:
                yield session
            finally:
                session.close()


__all__ = ["DEFAULT_ENTRY_LIMIT", "MAX_ENTRY_LIMIT", "SessionService"]
