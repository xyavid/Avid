"""In-process backend: one SessionState used as the storage, kept alive across session handles."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass

from .errors import (
    SessionAlreadyOpenError,
    SessionClosedError,
    SessionExistsError,
    SessionNotFoundError,
    SessionStorageError,
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
    """In-process storage: validation and application under one lock, so no commit is half-done."""

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

    def scan_values(self, namespace: str) -> list[StoredValue]:
        self._assert_open()
        return self._state.values_in(namespace)

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
    """What the repository remembers: metadata, storage, and whether a handle currently holds it."""

    metadata: SessionMetadata
    storage: MemoryStorage
    open: bool = True


class MemorySessionRepo:
    """The in-memory SessionRepo, behaviourally aligned with the file backend case by case."""

    def __init__(
        self,
        *,
        now=None,
        id_generator: IdGenerator | None = None,
        workspace: str | None = None,
    ) -> None:
        # Same shape as the file backend: the caller names the workspace this repository serves.
        self.workspace = workspace
        self._now = now or now_ms
        self._id_generator = id_generator or UuidV7Generator(self._now)
        self._sessions: dict[str, _Record] = {}
        self._pending: set[str] = set()
        self._closed = False

    # Lifecycle: create, open, list, delete, close.

    def create(
        self,
        *,
        id: str | None = None,
        parent_session_id: str | None = None,
        workspace: str | None = None,
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
                workspace=self.workspace if workspace is None else workspace,
            )
            storage = MemoryStorage(now=self._now)
            record = _Record(metadata=metadata, storage=storage)
            # The store outlives the handle, so closing and reopening resumes on the same state.
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
        self._assert_owned(metadata)
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
        self._assert_owned(metadata)
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

    # Internals: ownership and id reservation.

    def _assert_owned(self, metadata: SessionMetadata) -> None:
        """Ownership guard matching the file backend: another workspace in metadata is refused."""
        if (
            self.workspace
            and metadata.workspace
            and metadata.workspace != self.workspace
        ):
            raise SessionStorageError(
                f"会话 {metadata.id} 属于另一个工作区（{metadata.workspace}），"
                f"不能在 {self.workspace} 的仓库里访问"
            )

    def _reserve(self, session_id: str) -> None:
        if session_id in self._sessions or session_id in self._pending:
            raise SessionExistsError(session_id)
        self._pending.add(session_id)

    def _assert_open(self) -> None:
        if self._closed:
            raise SessionClosedError("仓库已关闭，不能再创建或打开会话。")
