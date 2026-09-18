"""会话读视图（F1）。

按设计文档的写权限归属：条目的写入者有且只有 ``SessionRecorder``，所以这里
**没有**任何「追加条目」的能力。本模块只有两处写：改名（= 值写入，PATCH 端点）
与销毁（DELETE 端点），两者都是会话对象自身的生命周期，不是消息。

读路径有一个关键约束：一个会话在 ``JsonlSessionRepo`` 里同时只能被一个句柄
打开，而活动 run 正持有它。所以所有读都先问注册表要活动句柄，拿不到才
自己 open/close——否则「运行中刷新页面」会直接失败。

分页纪律（不变量 I14）在服务端：默认 ``limit=100``，硬上限 500，游标
``cursor_seq`` **排他**（指向上一页最后一条）。长历史靠服务端分页解决，
不是靠前端虚拟化。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..session import (
    DEFAULT_BRANCH,
    BranchScan,
    JsonlSessionRepo,
    SessionBranchExistsError,
    SessionError,
    SessionExistsError,
    SessionInvalidIdError,
    SessionMetadata,
    SessionUnknownTargetError,
    messages_for_branch,
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

DEFAULT_ENTRY_LIMIT = 100
MAX_ENTRY_LIMIT = 500

# 判定链尾是否残缺时最多回看多少条——超过就放弃判定（返回 False），
# 不做昂贵的历史扫描。
_TAIL_SCAN_LIMIT = 64


class SessionService:
    def __init__(self, workspaces: WorkspaceService, runs: RunRegistry) -> None:
        self.workspaces = workspaces
        self.runs = runs

    # ---------------- 列表与元信息 ----------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """会话列表（元信息 + 名字 + 条数）。

        代价是 O(会话数 × 文件大小)：名字是会话文件里的一个值、条数要读全部条目。
        CLI 早已承认这个代价；Web 首屏同样付它（设计文档 §6.1）。
        """
        found: list[dict[str, Any]] = []
        for workspace in self.workspaces.workspaces():
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
            # 列表是最不该因为一个坏项整体失败的读操作（与 CLI 列举同原则）。
            logger.warning("跳过读不了的会话 %s：%s", meta.id, exc)
            return None

    def _facts(
        self, meta: SessionMetadata, workspace: Workspace | None
    ) -> tuple[str | None, int, bool]:
        """列表页要的三个事实：先用不重放的快速路径，判不出来才退回重放。

        以前每个会话都 ``open()`` 一次（逐行重放 + 建对象 + 抢会话句柄），
        20 个会话 5.9 MB 实测 54 ms；会话一多，首屏与"每次运行结束重取列表"
        都变成秒级。尾部窗口判不出链尾时（例如刚在别的分支上追加了很多条目）
        仍然重放一次拿权威答案——不猜。
        """
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
        """归属的线格式：id 来自会话 header（创建时的静态事实），名字来自注册表。

        注册表里查不到（已被摘掉索引、或老会话）也要给得出 id 与根目录——
        归属在 header 里，不依赖索引存活。
        """
        owner = workspace
        if owner is None:
            found = self.workspaces.find_session(meta.id)
            owner = found[0] if found is not None else None
        workspace_id = meta.workspace or (owner.id if owner is not None else None)
        return {
            "id": workspace_id,
            "root": owner.root if owner is not None else None,
            "name": owner.name if owner is not None else None,
            # 界面据此预选权限模式：会话的默认档来自它的工作区。
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

    # ---------------- 写：改名与销毁 ----------------

    def create(
        self,
        *,
        workspace: str,
        id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """新建会话：**必须先有归属**。``workspace`` 是必填参数——缺失是 400，
        不存在"用服务端默认值兜住"的路径（归属是会话的不可变事实，不该由环境决定）。"""
        owner = self.workspaces.resolve(workspace)
        repo = self.workspaces.repo_for(owner)
        try:
            session = repo.create(id=id, workspace=owner.id)
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
        with self._session(session_id) as session:
            session.set_name(name)
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        # 关句柄的路径都要先持会话句柄锁：销毁要求会话已关闭，与运行/读取互斥。
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

    # ---------------- 分支 ----------------

    def list_branches(self, session_id: str) -> dict[str, Any]:
        """分支列表（含链尾与条数）。

        条数要沿 parent 链走一趟，所以是 O(分支数 × 链长)。当前规模（分支个位数、
        条目数百）付得起；触发条件是长会话里分支列表明显变慢，届时把条数冗余成值。
        """
        with self._session(session_id) as session:
            branches = [self._branch_to_dict(session, name) for name in session.branch_names()]
        return {"session_id": session_id, "branches": branches}

    def create_branch(
        self, session_id: str, *, name: str | None = None, at: str | None = None
    ) -> dict[str, Any]:
        """在某条目处开一条新分支（fork）。``name`` 缺省时自动取 b2 / b3…

        活动 run 期间拒绝：分支头与条目共用同一个会话句柄，而运行还在往旧链尾追加，
        此刻分叉会让「新链从哪来」含混（还会和运行线程抢同一把写锁）。
        """
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
    def _branch_to_dict(session: Any, name: str) -> dict[str, Any]:
        target = session.branch(name)
        return {
            "name": name,
            "tip_entry_id": None if target is None else target.get_tip_id(),
            "entry_count": 0 if target is None else len(target.find_entries(BranchScan())),
            "is_default": name == DEFAULT_BRANCH,
        }

    @staticmethod
    def _next_branch_name(session: Any) -> str:
        """b2、b3…：跳过已存在的名字，免得默认命名撞上一个手工起的同名分支。"""
        existing = set(session.branch_names())
        index = 2
        while f"b{index}" in existing:
            index += 1
        return f"b{index}"

    # ---------------- 条目分页 ----------------

    def entries(
        self,
        session_id: str,
        *,
        branch: str = DEFAULT_BRANCH,
        order: str = "desc",
        limit: int | None = None,
        cursor_seq: int | None = None,
    ) -> dict[str, Any]:
        """按分支分页取条目。``order=desc`` 是默认：首屏要的是链尾。"""
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
                        limit=size + 1,  # 多取一条判断还有没有下一页
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
                # 只对链尾（第一页）有意义：整批结果没到齐的 tool_calls 会被投影丢掉
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

    # ---------------- 内部 ----------------

    def _truncated_tail(self, session: Any) -> bool:
        """链尾是否有一批没有结果的 tool_calls（= 上次运行在此中断）。

        投影 ``repair_incomplete_batches`` 会把整批丢掉；这里用同样的判据但只看
        链尾附近若干条，避免为了一个布尔值扫全history。
        """
        target = session.branch(DEFAULT_BRANCH)
        if target is None:
            return False
        entries = target.find_entries(BranchScan(order="newestFirst", limit=_TAIL_SCAN_LIMIT))
        seen: set[str] = set()
        for entry in entries:
            message = entry.message or {}
            if message.get("role") == "tool":
                seen.add(str(message.get("tool_call_id")))
                continue
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
        """活动 run 正持有的句柄优先；否则自己 open/close。

        ``meta``/``workspace`` 由调用方传进来时不再反查归属：列表已经为每个工作区
        遍历过一遍，再查一次会让列举变成 O(工作区数 × 会话数) 的平方级扫描。

        整段持**会话句柄锁**：会话层只允许一个句柄，而运行线程会在别处开/关它。
        没有这把锁，"读路径先开、运行线程后开"必然撞车（运行 failed 或读 500）。
        """
        with self.runs.session_lock(session_id):
            active = self.runs.active_session(session_id)
            if active is not None:
                yield active
                return

            owner = workspace
            metadata = meta
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
