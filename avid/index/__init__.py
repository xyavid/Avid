"""会话索引：JSONL 之上的派生查询层（元数据 / 定位 / 全文检索）。

对外的名字只从这里出；包内分工看各模块的模块注释。这个包的边界只有一条，
但它是整件事的前提：**索引可以落后、可以坏、可以整个删掉重建，JSONL 不受影响**。
"""

from __future__ import annotations

from .db import (
    BUSY_TIMEOUT_MS,
    MIGRATIONS,
    SCHEMA_VERSION,
    migrate,
    open_db,
    schema_version,
    transaction,
)
from .indexer import SessionIndexer, workspace_id_from_path
from .queries import (
    count_sessions,
    entries_of,
    entry_location,
    get_session,
    index_stats,
    list_sessions,
    search_entries,
    sessions_by_workspace,
    tokens,
)
from .types import (
    INDEX_STATUS_ERROR,
    INDEX_STATUS_MISSING,
    INDEX_STATUS_OK,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_STALE,
    INDEX_STATUS_UNSUPPORTED,
    INDEX_STATUSES,
    IndexedSession,
    IndexReport,
    ScannedEntry,
    ScanResult,
    SearchHit,
)

__all__ = [
    "BUSY_TIMEOUT_MS",
    "SessionIndexer",
    "INDEX_STATUSES",
    "INDEX_STATUS_ERROR",
    "INDEX_STATUS_MISSING",
    "INDEX_STATUS_OK",
    "INDEX_STATUS_PENDING",
    "INDEX_STATUS_STALE",
    "INDEX_STATUS_UNSUPPORTED",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "IndexReport",
    "IndexedSession",
    "ScanResult",
    "ScannedEntry",
    "SearchHit",
    "count_sessions",
    "entries_of",
    "entry_location",
    "get_session",
    "index_stats",
    "list_sessions",
    "migrate",
    "open_db",
    "schema_version",
    "search_entries",
    "sessions_by_workspace",
    "tokens",
    "transaction",
    "workspace_id_from_path",
]
