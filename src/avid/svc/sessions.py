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
    BranchScan,
    JsonlSessionRepo,
    SessionError,
    SessionExistsError,
    SessionInvalidIdError,
    SessionMetadata,
    messages_for_branch,
)
from .errors import (
    SessionBusy,
    SessionExists,
    SessionNotFound,
    SessionReadError,
)
from .runs import RunRegistry

logger = logging.getLogger("avid.svc.sessions")

DEFAULT_ENTRY_LIMIT = 100
MAX_ENTRY_LIMIT = 500
DEFAULT_BRANCH = "main"

# 判定链尾是否残缺时最多回看多少条——超过就放弃判定（返回 False），
# 不做昂贵的历史扫描。
_TAIL_SCAN_LIMIT = 64


class SessionService:
    def __init__(self, repo: JsonlSessionRepo, runs: RunRegistry) -> None:
        self.repo = repo
        self.runs = runs

    # ---------------- 列表与元信息 ----------------

    def list_sessions(self) -> list[dict[str, Any]]:
        """会话列表（元信息 + 名字 + 条数）。

        代价是 O(会话数 × 文件大小)：名字是会话文件里的一个值、条数要读全部条目。
        CLI 早已承认这个代价；Web 首屏同样付它（设计文档 §6.1）。
        """
        found: list[dict[str, Any]] = []
        for meta in self.repo.list():
            summary = self._summary(meta)
            if summary is not None:
                found.append(summary)
        return found

    def _summary(self, meta: SessionMetadata) -> dict[str, Any] | None:
        try:
            with self._session(meta.id, meta=meta) as session:
                return {
                    "id": meta.id,
                    "name": session.get_name(),
                    "created_at": meta.created_at,
                    "storage_version": meta.storage_version,
                    "parent_session_id": meta.parent_session_id,
                    "message_count": session.get_stats().message_count,
                    "active_run_id": self.runs.active_run_id(meta.id),
                    "truncated_tail": self._truncated_tail(session),
                }
        except (SessionReadError, SessionError) as exc:
            # 列表是最不该因为一个坏项整体失败的读操作（与 CLI 列举同原则）。
            logger.warning("跳过读不了的会话 %s：%s", meta.id, exc)
            return None

    def get(self, session_id: str) -> dict[str, Any]:
        with self._session(session_id) as session:
            meta = session.metadata
            return {
                "id": meta.id,
                "name": session.get_name(),
                "created_at": meta.created_at,
                "storage_version": meta.storage_version,
                "parent_session_id": meta.parent_session_id,
                "message_count": session.get_stats().message_count,
                "active_run_id": self.runs.active_run_id(meta.id),
                "truncated_tail": self._truncated_tail(session),
                "branch": DEFAULT_BRANCH,
            }

    # ---------------- 写：改名与销毁 ----------------

    def create(self, *, id: str | None = None, name: str | None = None) -> dict[str, Any]:
        try:
            session = self.repo.create(id=id)
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
        if self.runs.active_run_id(session_id) is not None:
            raise SessionBusy(f"会话有活动 run，不能销毁：{session_id}")
        metadata = self.runs.find_metadata(session_id)
        if metadata is None:
            raise SessionNotFound(f"没有这个会话：{session_id}")
        try:
            self.repo.delete(metadata)
        except SessionError as exc:
            raise SessionReadError(f"销毁会话失败：{exc}") from exc

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
            from .errors import InvalidRequest

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
        self, session_id: str, *, meta: SessionMetadata | None = None
    ) -> Iterator[Any]:
        """活动 run 正持有的句柄优先；否则自己 open/close。"""
        active = self.runs.active_session(session_id)
        if active is not None:
            yield active
            return

        metadata = meta or self.runs.find_metadata(session_id)
        if metadata is None:
            raise SessionNotFound(f"没有这个会话：{session_id}")
        try:
            session = self.repo.open(metadata)
        except SessionError as exc:
            raise SessionReadError(f"打不开会话 {session_id}：{exc}") from exc
        try:
            yield session
        finally:
            session.close()


__all__ = ["DEFAULT_ENTRY_LIMIT", "MAX_ENTRY_LIMIT", "SessionService"]
