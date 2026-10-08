"""Session persistence facade: the names real consumers import, pinned as public API by a test."""

from __future__ import annotations

from .errors import (
    SessionAlreadyOpenError,
    SessionBranchExistsError,
    SessionBusyError,
    SessionClosedError,
    SessionError,
    SessionExistsError,
    SessionInvalidBranchError,
    SessionInvalidIdError,
    SessionInvalidMessageError,
    SessionInvariantError,
    SessionLockedError,
    SessionNotFoundError,
    SessionStorageError,
    SessionUnknownTargetError,
)
from .ids import UuidV7Generator, new_uuidv7, now_ms, validate_session_id
from .jsonl import JsonlSessionRepo
from .memory import MemorySessionRepo
from .mutation import MutationLine
from .projection import (
    entries_to_messages,
    messages_for_branch,
    repair_incomplete_batches,
)
from .recorder import SessionRecorder
from .session import validate_message
from .types import (
    STORAGE_VERSION,
    BranchScan,
    CommittedEntry,
    CommittedValueSet,
    Entry,
    EntryQuery,
    EntryWrite,
    JsonlSessionMetadata,
    NewEntry,
    SessionMetadata,
    SessionStats,
)
from .values import (
    COMPACTION_NS,
    DEFAULT_BRANCH,
    SCRATCH_NS,
    USAGE_NS,
    branch_compaction,
    branch_tip,
    branch_usage,
    entry_label,
    session_name,
    session_scratch,
    set_value,
    value,
)

__all__ = [
    # Error names that callers branch on.
    "SessionError",
    "SessionNotFoundError",
    "SessionExistsError",
    "SessionAlreadyOpenError",
    "SessionClosedError",
    "SessionBusyError",
    "SessionLockedError",
    "SessionInvariantError",
    "SessionStorageError",
    "SessionInvalidIdError",
    "SessionInvalidBranchError",
    "SessionBranchExistsError",
    "SessionUnknownTargetError",
    "SessionInvalidMessageError",
    # Data plane.
    "Entry",
    "NewEntry",
    "EntryWrite",
    "CommittedEntry",
    "CommittedValueSet",
    "EntryQuery",
    "BranchScan",
    "SessionStats",
    "SessionMetadata",
    "JsonlSessionMetadata",
    "STORAGE_VERSION",
    # Value addresses.
    "DEFAULT_BRANCH",
    "USAGE_NS",
    "COMPACTION_NS",
    "SCRATCH_NS",
    "branch_compaction",
    "branch_tip",
    "branch_usage",
    "entry_label",
    "session_name",
    "session_scratch",
    "set_value",
    "value",
    # Repositories.
    "MemorySessionRepo",
    "JsonlSessionRepo",
    # Writer and read-side projections.
    "SessionRecorder",
    "MutationLine",
    "messages_for_branch",
    "entries_to_messages",
    "repair_incomplete_batches",
    # Validation and ids.
    "validate_message",
    "validate_session_id",
    "UuidV7Generator",
    "new_uuidv7",
    "now_ms",
]
