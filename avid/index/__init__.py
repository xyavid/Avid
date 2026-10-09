"""Derived query layer over the session JSONL: metadata, entry locations and full-text search.

The index may lag, break or be deleted and rebuilt from scratch; the JSONL is never affected.
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
from .indexer import SessionIndexer, notifying, workspace_id_from_path
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
    "notifying",
    "open_db",
    "schema_version",
    "search_entries",
    "sessions_by_workspace",
    "tokens",
    "transaction",
    "workspace_id_from_path",
]
