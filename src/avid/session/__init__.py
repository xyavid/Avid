"""会话持久化（阶段 12）。

对外只暴露**真实消费者用到的那些名字**（外加错误码这类契约名）：

* 数据面：`Entry` / `NewEntry` / `EntryWrite`、提交后的 `CommittedEntry` /
  `CommittedValueSet`，以及查询用的 `EntryQuery` / `BranchScan`
* 仓库：`MemorySessionRepo` / `JsonlSessionRepo`（同一套一致性用例跑两个后端）
* 写入与投影：`SessionRecorder` 是唯一写入者；`messages_for_branch` /
  `entries_to_messages` / `repair_incomplete_batches` 是读出来的投影
* 值：`session_name` / `branch_tip` / `entry_label` 这些地址构造器
* 错误：`SessionError` 一族（调用方按它们分支）

**存储内部件不在门面里**：`JsonlStorage` / `MemoryStorage` / `JsonlHeader` /
`PreparedCommit` / `CommittedWrite` / `StorageBackedSession` / `SessionBranch` /
`ValueAddress` …都还在各自的子模块（`avid.session.jsonl`、`.memory`、`.session`、
`.types`、`.values`），需要时按子模块路径导入。以前门面列了 68 个名字，其中 26 个
在 `src/` 与 `tests/` 里从没被用过——于是"改内部实现"看上去都像公开接口变更。

本包**不 import** `avid` 的其它子包：它只认识条目、值与 JSON，
路径与时钟在构造期注入。依赖方向由 CLI 一个人接线（不变量 I7）。
`tests/test_session_facade.py` 钉住这份清单：改它就是公开接口变更。
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
    DEFAULT_BRANCH,
    branch_tip,
    entry_label,
    session_name,
    set_value,
    value,
)

__all__ = [
    # 错误（契约名：调用方按它们分支）
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
    # 数据面
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
    # 值
    "DEFAULT_BRANCH",
    "branch_tip",
    "entry_label",
    "session_name",
    "set_value",
    "value",
    # 仓库
    "MemorySessionRepo",
    "JsonlSessionRepo",
    # 写入与投影
    "SessionRecorder",
    "MutationLine",
    "messages_for_branch",
    "entries_to_messages",
    "repair_incomplete_batches",
    # 校验与 id
    "validate_message",
    "validate_session_id",
    "UuidV7Generator",
    "new_uuidv7",
    "now_ms",
]
