"""Session data structures: the entry and value records plus the protocols both backends share."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

if TYPE_CHECKING:  # annotation only: values.py supplies the runtime object, which avoids a cycle
    from .values import ValueAddress

# Result type of a mutation callback, so one grant can produce whatever the caller needs.
T = TypeVar("T")

# Entry discriminator; the format version leaves room for further entry types.
EntryType = str
MESSAGE_ENTRY: EntryType = "message"
# Kernel-injected notice (todo reminder / stop nudge): shown to the model, but not conversation.
NOTICE_ENTRY: EntryType = "notice"
# Human-facing failure bookkeeping; the _TRANSCRIPT_TYPES projection keeps it out of model context.
ERROR_ENTRY: EntryType = "error"

Order = Literal["asc", "desc"]
BranchOrder = Literal["newestFirst", "oldestFirst"]

# Storage format version; a file carrying any other value is rejected rather than guessed at.
STORAGE_VERSION = 1


@dataclass(frozen=True)
class Entry:
    """One committed entry; parent_id chains entries and a branch tip is an id at a chain end."""

    id: str
    parent_id: str | None
    seq: int
    timestamp: int
    type: EntryType
    message: dict[str, Any] | None = None


@dataclass(frozen=True)
class NewEntry:
    """An uncommitted entry: seq and timestamp are assigned by the commit that stores it."""

    id: str
    parent_id: str | None
    type: EntryType = MESSAGE_ENTRY
    message: dict[str, Any] | None = None


@dataclass(frozen=True)
class EntryWrite:
    entry: NewEntry


@dataclass(frozen=True)
class ValueSetWrite:
    namespace: str
    key: str
    value: Any


@dataclass(frozen=True)
class ValueDeleteWrite:
    namespace: str
    key: str


# One write inside a transaction: either an entry, a value set or a value delete.
Write = EntryWrite | ValueSetWrite | ValueDeleteWrite

# Committed writes carry the seq and timestamp assigned at commit time; they are the disk unit.


@dataclass(frozen=True)
class CommittedEntry:
    seq: int
    timestamp: int
    entry: NewEntry


@dataclass(frozen=True)
class CommittedValueSet:
    seq: int
    namespace: str
    key: str
    value: Any


@dataclass(frozen=True)
class CommittedValueDelete:
    seq: int
    namespace: str
    key: str


CommittedWrite = CommittedEntry | CommittedValueSet | CommittedValueDelete


@dataclass(frozen=True)
class StoredValue:
    namespace: str
    key: str
    value: Any
    seq: int


@dataclass(frozen=True)
class EntryQuery:
    """Session-level query: one global scan by seq, newest first by default."""

    type: EntryType | None = None
    order: Order = "desc"
    limit: int | None = None
    cursor_seq: int | None = None


@dataclass(frozen=True)
class BranchScan:
    """Branch scan: start is the chain tip and the walk follows parent_id back through the chain."""

    start: str | None = None
    type: EntryType | None = None
    order: BranchOrder = "newestFirst"
    limit: int | None = None
    cursor_seq: int | None = None


@dataclass(frozen=True)
class SessionStats:
    """Session totals: message count only, because runtime usage is stored per branch as a value."""

    message_count: int


@dataclass(frozen=True)
class CommitResult:
    """The outcome of one commit; stats describes the state after the writes landed."""

    first_seq: int
    seqs: tuple[int, ...]
    timestamp: int
    stats: SessionStats


@dataclass(frozen=True)
class PreparedCommit:
    """A commit with seq and timestamp assigned and validated, but not yet written."""

    writes: tuple[CommittedWrite, ...]
    first_seq: int
    seqs: tuple[int, ...]
    timestamp: int


@dataclass(frozen=True)
class SessionMetadata:
    """Session metadata; a listing needs only this and never opens the session."""

    id: str
    created_at: int
    storage_version: int = STORAGE_VERSION
    parent_session_id: str | None = None
    # Owning workspace, fixed at creation; files without it inherit their location.
    workspace: str | None = None


@dataclass(frozen=True)
class JsonlSessionMetadata(SessionMetadata):
    """File-backend metadata: the path and modification time that listing and deletion need."""

    path: Path = field(default_factory=Path)
    modified_at: int = 0


class IdGenerator(Protocol):
    """Source of unique session and entry ids."""

    def next(self) -> str: ...


class Storage(Protocol):
    """Minimal session data-plane contract implemented by the memory and the file backend."""

    def commit(self, writes: Sequence[Write]) -> CommitResult: ...

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_values(self, namespace: str) -> list[StoredValue]: ...

    def scan_branch(self, query: BranchScan) -> list[Entry]: ...

    def scan_entries(self, query: EntryQuery) -> list[Entry]: ...

    def get_stats(self) -> SessionStats: ...

    def close(self) -> None: ...


class Branch(Protocol):
    """One named chain of entries; the tip is a value and appending writes entry and tip as one."""

    name: str

    def get_tip_id(self) -> str | None: ...

    def find_entries(self, query: BranchScan | None = None) -> list[Entry]: ...

    def find_entry(self, query: BranchScan | None = None) -> Entry | None: ...

    def append_message(
        self, message: dict[str, Any], *, entry_type: EntryType = MESSAGE_ENTRY
    ) -> str: ...


class Session(Protocol):
    """An opened session: reads, branch management, exclusive mutations and values."""

    metadata: SessionMetadata
    id_generator: IdGenerator

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_entry(self, entry_id: str) -> Entry | None: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_values(self, namespace: str) -> list[StoredValue]: ...

    def get_stats(self) -> SessionStats: ...

    def find_entries(self, query: EntryQuery | None = None) -> list[Entry]: ...

    def find_entry(self, query: EntryQuery | None = None) -> Entry | None: ...

    def branch(self, name: str) -> Branch | None: ...

    def branch_names(self) -> list[str]: ...

    def create_branch(self, name: str, at: str | None = None) -> Branch: ...

    def begin_mutation(self) -> SessionMutation: ...

    def mutate(self, callback: MutationCallback[T]) -> T: ...

    def set_value(self, address: ValueAddress, value: Any) -> None: ...

    def delete_value(self, address: ValueAddress) -> None: ...

    def get_name(self) -> str | None: ...

    def set_name(self, name: str | None) -> None: ...

    def get_label(self, target_id: str) -> str | None: ...

    def set_label(self, target_id: str, label: str | None) -> None: ...

    def close(self) -> None: ...


class SessionRepo(Protocol):
    """Creation, opening, listing and deletion of sessions."""

    def create(
        self,
        *,
        id: str | None = None,
        parent_session_id: str | None = None,
        workspace: str | None = None,
    ) -> Session: ...

    def open(self, metadata: SessionMetadata) -> Session: ...

    def list(self) -> list[SessionMetadata]: ...

    def delete(self, metadata: SessionMetadata) -> None: ...

    def close(self) -> None: ...


class SessionMutation(Protocol):
    """One exclusive read-modify-write grant: at most one commit, and nothing works after end."""

    def commit(self, writes: Sequence[Write]) -> CommitResult: ...

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]: ...

    def get_value(self, address: ValueAddress) -> StoredValue | None: ...

    def scan_branch(self, query: BranchScan) -> list[Entry]: ...

    def get_stats(self) -> SessionStats: ...

    def end(self) -> None: ...


# A callback that receives one exclusive mutation grant and returns its own result.
MutationCallback = Callable[[SessionMutation], T]
