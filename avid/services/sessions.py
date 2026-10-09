"""Read views over sessions for the application services, plus the rename and delete writes."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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
    SessionRecorder,
    SessionUnknownTargetError,
    messages_for_branch,
    session_scratch,
)
from ..session.types import MESSAGE_ENTRY
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

logger = logging.getLogger("avid.services.sessions")

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
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Create a session; a workspace is mandatory, since ownership is an immutable fact."""
        owner = self.workspaces.resolve(workspace)
        repo = self.workspaces.repo_for(owner)
        try:
            session = repo.create(id=id, workspace=owner.id, parent_session_id=parent)
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

    def create_scratch(
        self,
        source_id: str,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        """开一个临时会话（阶段 54）：同一工作区、拷一份源会话的投影当历史、打上临时标记。

        为什么拷**投影**而不是全量条目：投影就是模型当时看到的那些消息（被压缩游标覆盖的
        前缀已由摘要替代），拷它等于把"当时的上下文"原样搬过来，也不会把已经压缩掉的原文
        重新塞进新会话。

        标记（`session_scratch()`）落在会话上：之后无论谁发起这个会话的运行，工具表与沙箱
        都按只读装配；关闭面板即销毁，会话文件一并消失。
        """
        with self._session(source_id) as source:
            owner = self.workspaces.find_session(source_id)
            if owner is None:  # pragma: no cover - _session already proved the session exists
                raise SessionNotFound(f"没有这个会话：{source_id}")
            workspace, _ = owner
            messages = messages_for_branch(source, DEFAULT_BRANCH)
            source_name = source.get_name()

        created = self.create(
            workspace=workspace.id,
            name=name or f"临时对话{f' · {source_name}' if source_name else ''}",
            # 血缘留着：临时会话从哪条会话长出来的，是排查与展示都用得上的事实
            parent=source_id,
        )
        new_id = str(created["id"])
        try:
            with self._session(new_id) as scratch:
                # 走 recorder（会话包的唯一写入者）而不是自己 append：拷贝的也是会话内容，
                # 旁路会绕过那段契约（A11 门禁现在真扫 services，这条是它逼出来的）。
                recorder = SessionRecorder(scratch)
                for message in messages:
                    recorder.on_message(message)
                scratch.set_value(session_scratch(), {"source": source_id})
        except SessionError as exc:
            # 建一半的临时会话不能留：它没有标记，会被当成普通会话留在列表里
            found = self.workspaces.find_session(new_id)
            if found is not None:
                with suppress(SessionError):
                    self.workspaces.repo_for(found[0]).delete(found[1])
            raise SessionReadError(f"临时会话创建失败：{exc}") from exc
        self.runs.notify_index(new_id)
        view = self.get(new_id)
        view["copied_messages"] = len(messages)
        return view

    def rename(self, session_id: str, name: str) -> dict[str, Any]:
        """Rename a session and return its refreshed view."""
        with self._session(session_id) as session:
            session.set_name(name)
        # 名字是索引里的显示事实：不改它，检索结果会一直报旧名字。
        self.runs.notify_index(session_id)
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
        # 会话没了，索引里那一行也得走：检出「文件已消失」的是索引器自己（它按发现结果判定）。
        self.runs.notify_index(session_id)

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
        """Whether the tip is a run that never finished（界面据此说「上次运行在此中断」）。

        两种形状都是中断：**工具调用没有结果**（停在批次中间）、以及**末尾是用户消息**
        （这一轮一个回复都没留下——最常见的原因是首个模型调用就失败了）。

        正在跑的会话不算：那半截是理所应当的，不是中断。末尾是**失败记账**（error 条目）时
        也不算——失败的原因已经记在会话里了，再挂一句含糊的「中断」是噪音。
        """
        if self.runs.active_run_id(session.metadata.id) is not None:
            return False
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
            if entry.type == MESSAGE_ENTRY and message.get("role") == "user":
                return True
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
