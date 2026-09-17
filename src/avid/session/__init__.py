"""会话持久化（阶段 12）。

对外只暴露这一组名字。分层：

* 数据面：``Entry`` / ``Write`` / ``ValueAddress`` / ``SessionStats``
* 句柄：``StorageBackedSession`` / ``SessionBranch`` / ``SessionMutation``
* 仓库：``MemorySessionRepo`` / ``JsonlSessionRepo``
* 集成：``SessionRecorder`` / ``messages_for_branch``

本包**不 import** ``avid`` 的其它子包：它只认识条目、值与 JSON，
路径与时钟在构造期注入。依赖方向由 CLI 一个人接线（不变量 I7）。
"""

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
    SessionNotFoundError,
    SessionStorageError,
    SessionUnknownTargetError,
)
from .ids import UuidV7Generator, new_uuidv7, now_ms, validate_session_id
from .jsonl import JsonlHeader, JsonlSessionRepo, JsonlStorage
from .memory import MemorySessionRepo, MemoryStorage
from .mutation import MutationLine
from .projection import repair_incomplete_batches, entries_to_messages, messages_for_branch
from .recorder import SessionRecorder
from .session import (
    SessionBranch,
    SessionMutation,
    StorageBackedSession,
    validate_message,
)
from .types import (
    Branch,
    BranchScan,
    CommitResult,
    CommittedEntry,
    CommittedValueDelete,
    CommittedValueSet,
    CommittedWrite,
    Entry,
    EntryQuery,
    EntryType,
    EntryWrite,
    IdGenerator,
    JsonlSessionMetadata,
    MESSAGE_ENTRY,
    NewEntry,
    PreparedCommit,
    Session,
    SessionMetadata,
    SessionRepo,
    SessionStats,
    STORAGE_VERSION,
    Storage,
    StoredValue,
    ValueDeleteWrite,
    ValueSetWrite,
    Write,
)
from .values import (
    branch_tip,
    delete_value,
    entry_label,
    session_name,
    set_value,
    value,
    ValueAddress,
)

__all__ = [
    # 错误
    "SessionError",
    "SessionNotFoundError",
    "SessionExistsError",
    "SessionAlreadyOpenError",
    "SessionClosedError",
    "SessionBusyError",
    "SessionInvariantError",
    "SessionStorageError",
    "SessionInvalidIdError",
    "SessionInvalidBranchError",
    "SessionBranchExistsError",
    "SessionUnknownTargetError",
    "SessionInvalidMessageError",
    # 数据面
    "Entry",
    "EntryType",
    "MESSAGE_ENTRY",
    "NewEntry",
    "EntryWrite",
    "ValueSetWrite",
    "ValueDeleteWrite",
    "Write",
    "CommittedEntry",
    "CommittedValueSet",
    "CommittedValueDelete",
    "CommittedWrite",
    "PreparedCommit",
    "CommitResult",
    "EntryQuery",
    "BranchScan",
    "SessionStats",
    "SessionMetadata",
    "JsonlSessionMetadata",
    "StoredValue",
    "ValueAddress",
    "value",
    "session_name",
    "entry_label",
    "branch_tip",
    "set_value",
    "delete_value",
    "STORAGE_VERSION",
    # 协议
    "Storage",
    "Session",
    "SessionRepo",
    "SessionMutation",
    "Branch",
    "IdGenerator",
    # 实现
    "StorageBackedSession",
    "SessionBranch",
    "MutationLine",
    "MemorySessionRepo",
    "MemoryStorage",
    "JsonlSessionRepo",
    "JsonlStorage",
    "JsonlHeader",
    "UuidV7Generator",
    "new_uuidv7",
    "now_ms",
    "validate_session_id",
    "validate_message",
    # 集成
    "SessionRecorder",
    "messages_for_branch",
    "entries_to_messages",
    "repair_incomplete_batches",
]
