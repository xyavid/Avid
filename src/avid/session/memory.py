"""进程内后端：一份 ``SessionState`` 当作存储。

它的价值是把"会话语义"与"文件格式"分开测——一致性套件先在它身上跑，再原样
跑一遍 JSONL 后端，两者答案不一致就是 bug。会话关闭时它**不关存储**
（``close_storage=False``）：内存后端靠同一份状态支持 close → open 续接，
这是 pi 用 facade 达成、我们用显式开关达成的同一件事。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import (
    SessionAlreadyOpenError,
    SessionClosedError,
    SessionExistsError,
    SessionNotFoundError,
)
from .ids import UuidV7Generator, now_ms, validate_session_id
from .session import StorageBackedSession
from .state import SessionState
from .types import (
    BranchScan,
    CommitResult,
    Entry,
    EntryQuery,
    IdGenerator,
    SessionMetadata,
    SessionStats,
    StoredValue,
    Write,
)
from .values import ValueAddress


class MemoryStorage:
    """进程内存储：校验 + 落地都在一把锁里完成，不存在"写了一半"。"""

    def __init__(self, *, now=None) -> None:
        self._now = now or now_ms
        self._state = SessionState()
        self._lock = threading.Lock()
        self._closed = False

    def commit(self, writes: Sequence[Write]) -> CommitResult:
        self._assert_open()
        with self._lock:
            prepared = self._state.prepare_commit(writes, self._now())
            stats = self._state.apply(prepared.writes)
        return CommitResult(
            first_seq=prepared.first_seq,
            seqs=prepared.seqs,
            timestamp=prepared.timestamp,
            stats=stats,
        )

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        self._assert_open()
        return self._state.get_entries(ids)

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        self._assert_open()
        return self._state.get_value(address)

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        self._assert_open()
        return self._state.scan_branch(query)

    def scan_entries(self, query: EntryQuery) -> list[Entry]:
        self._assert_open()
        return self._state.scan_entries(query)

    def get_stats(self) -> SessionStats:
        self._assert_open()
        return self._state.stats

    def close(self) -> None:
        self._closed = True

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError("内存存储已关闭。")


@dataclass
class _Record:
    """仓库记住的东西：元信息、存储、以及"现在有没有句柄拿着它"。"""

    metadata: SessionMetadata
    storage: MemoryStorage
    open: bool = True


class MemorySessionRepo:
    """``SessionRepo`` 的内存实现，行为与 ``JsonlSessionRepo`` 逐条对齐。"""

    def __init__(self, *, now=None, id_generator: IdGenerator | None = None) -> None:
        self._now = now or now_ms
        self._id_generator = id_generator or UuidV7Generator(self._now)
        self._sessions: dict[str, _Record] = {}
        self._pending: set[str] = set()
        self._closed = False

    # ---------------- 生命周期 ----------------

    def create(
        self, *, id: str | None = None, parent_session_id: str | None = None
    ) -> StorageBackedSession:
        self._assert_open()
        session_id = validate_session_id(
            self._id_generator.next() if id is None else id
        )
        self._reserve(session_id)
        try:
            metadata = SessionMetadata(
                id=session_id,
                created_at=self._now(),
                parent_session_id=parent_session_id,
            )
            storage = MemoryStorage(now=self._now)
            record = _Record(metadata=metadata, storage=storage)
            session = StorageBackedSession(
                metadata,
                storage,
                id_generator=self._id_generator,
                close_storage=False,
                on_close=lambda: setattr(record, "open", False),
            )
            self._sessions[session_id] = record
            return session
        finally:
            self._pending.discard(session_id)

    def open(self, metadata: SessionMetadata) -> StorageBackedSession:
        self._assert_open()
        record = self._sessions.get(metadata.id)
        if record is None:
            raise SessionNotFoundError(metadata.id)
        if record.open:
            raise SessionAlreadyOpenError(metadata.id)
        record.open = True
        return StorageBackedSession(
            record.metadata,
            record.storage,
            id_generator=self._id_generator,
            close_storage=False,
            on_close=lambda: setattr(record, "open", False),
        )

    def list(self) -> list[SessionMetadata]:
        self._assert_open()
        records = sorted(
            self._sessions.values(),
            key=lambda item: (-item.metadata.created_at, item.metadata.id),
        )
        return [record.metadata for record in records]

    def delete(self, metadata: SessionMetadata) -> None:
        self._assert_open()
        record = self._sessions.get(metadata.id)
        if record is None:
            raise SessionNotFoundError(metadata.id)
        if record.open:
            raise SessionAlreadyOpenError(metadata.id)
        record.storage.close()
        del self._sessions[metadata.id]

    def close(self) -> None:
        self._closed = True
        for record in self._sessions.values():
            if record.open:
                record.storage.close()
                record.open = False

    # ---------------- 内部 ----------------

    def _reserve(self, session_id: str) -> None:
        if session_id in self._sessions or session_id in self._pending:
            raise SessionExistsError(session_id)
        self._pending.add(session_id)

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError("仓库已关闭，不能再创建或打开会话。")
