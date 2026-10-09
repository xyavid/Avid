"""Write scan results into the database: rows, cursor and status in one transaction.

The cursor commits together with the rows of that batch, so a crash only redoes the batch and never
leaves the cursor ahead of the rows; a failure lands in `last_error` and the indexer moves on.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..session import now_ms
from .db import read_meta, transaction, write_meta
from .types import (
    INDEX_STATUS_ERROR,
    INDEX_STATUS_MISSING,
    INDEX_STATUS_OK,
    INDEX_STATUS_STALE,
    INDEX_STATUS_UNSUPPORTED,
    IndexedSession,
    ScanResult,
)

_SESSION_COLUMNS = (
    "session_id",
    "file_path",
    "workspace_id",
    "workspace_root",
    "workspace_name",
    "title",
    "first_user_text",
    "created_at",
    "updated_at",
    "file_size",
    "indexed_bytes",
    "entry_count",
    "indexed_at",
    "index_status",
    "last_error",
)


def _write_session_row(conn: sqlite3.Connection, session: IndexedSession) -> None:
    conn.execute(
        f"""
        INSERT INTO sessions ({", ".join(_SESSION_COLUMNS)})
        VALUES ({", ".join("?" for _ in _SESSION_COLUMNS)})
        ON CONFLICT(session_id) DO UPDATE SET
            file_path = excluded.file_path,
            workspace_id = excluded.workspace_id,
            workspace_root = excluded.workspace_root,
            workspace_name = excluded.workspace_name,
            title = excluded.title,
            first_user_text = excluded.first_user_text,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at,
            file_size = excluded.file_size,
            indexed_bytes = excluded.indexed_bytes,
            entry_count = excluded.entry_count,
            indexed_at = excluded.indexed_at,
            index_status = excluded.index_status,
            last_error = excluded.last_error
        """,
        tuple(getattr(session, name) for name in _SESSION_COLUMNS),
    )


def _write_entries(conn: sqlite3.Connection, session: IndexedSession, scan: ScanResult) -> None:
    if not scan.entries:
        return
    conn.executemany(
        """
        INSERT INTO entries
            (session_id, entry_id, seq, type, role, timestamp, byte_offset, byte_length, search_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id, entry_id) DO NOTHING
        """,
        [
            (
                session.session_id,
                item.entry_id,
                item.seq,
                item.entry_type,
                item.role,
                item.timestamp,
                item.byte_offset,
                item.byte_length,
                item.search_text,
            )
            for item in scan.entries
        ],
    )


def apply_scan(conn: sqlite3.Connection, session: IndexedSession, scan: ScanResult) -> None:
    """Write this pass's rows and move the cursor, in one transaction."""
    with transaction(conn):
        _write_session_row(conn, session)
        _write_entries(conn, session, scan)


def replace_scan(conn: sqlite3.Connection, session: IndexedSession, scan: ScanResult) -> None:
    """Rebuild one session: drop its rows first, then write the whole scan (one transaction)."""
    with transaction(conn):
        conn.execute("DELETE FROM entries WHERE session_id = ?", (session.session_id,))
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session.session_id,))
        _write_session_row(conn, session)
        _write_entries(conn, session, scan)


def upsert_session(conn: sqlite3.Connection, session: IndexedSession) -> None:
    """Write only the session row; used by metadata-only passes."""
    with transaction(conn):
        _write_session_row(conn, session)


def _set_status(
    conn: sqlite3.Connection,
    session_id: str,
    status: str,
    error: str | None,
    *,
    file_path: str | None = None,
) -> None:
    """Set a status; with a path it also creates the row, because a first pass can fail before one exists."""
    message = None if error is None else error[:500]
    with transaction(conn):
        if file_path is None:
            conn.execute(
                "UPDATE sessions SET index_status = ?, last_error = ? WHERE session_id = ?",
                (status, message, session_id),
            )
            return
        conn.execute(
            """
            INSERT INTO sessions (session_id, file_path, index_status, last_error, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                file_path = excluded.file_path,
                index_status = excluded.index_status,
                last_error = excluded.last_error,
                indexed_at = excluded.indexed_at
            """,
            (session_id, file_path, status, message, now_ms()),
        )


def mark_error(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    file_path: str,
    error: str,
    status: str = INDEX_STATUS_ERROR,
) -> None:
    """Record why a session could not be indexed; the row is created when the session is new."""
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO sessions (session_id, file_path, index_status, last_error, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                file_path = excluded.file_path,
                index_status = excluded.index_status,
                last_error = excluded.last_error,
                indexed_at = excluded.indexed_at
            """,
            (session_id, file_path, status, error[:500], now_ms()),
        )


def mark_missing(conn: sqlite3.Connection, session_id: str, *, file_path: str | None = None) -> None:
    _set_status(conn, session_id, INDEX_STATUS_MISSING, "文件已经不在了", file_path=file_path)


def mark_stale(
    conn: sqlite3.Connection, session_id: str, reason: str, *, file_path: str | None = None
) -> None:
    _set_status(conn, session_id, INDEX_STATUS_STALE, reason, file_path=file_path)


def mark_unsupported(
    conn: sqlite3.Connection, session_id: str, reason: str, *, file_path: str | None = None
) -> None:
    _set_status(conn, session_id, INDEX_STATUS_UNSUPPORTED, reason, file_path=file_path)


def mark_ok(conn: sqlite3.Connection, session_id: str) -> None:
    _set_status(conn, session_id, INDEX_STATUS_OK, None)


def forget_path_owner(conn: sqlite3.Connection, file_path: str, keep_session_id: str) -> None:
    """Drop the row that owns this path under a different session id.

    A path can only be owned by one session (`file_path UNIQUE`), so when a file is replaced by
    another session the old row must go first — otherwise the insert raises IntegrityError and
    the whole pass aborts (and keeps aborting on every later pass).
    """
    with transaction(conn):
        conn.execute(
            "DELETE FROM sessions WHERE file_path = ? AND session_id != ?",
            (file_path, keep_session_id),
        )


def forget_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Drop a session that no longer has a file; its entries go with it through the cascade."""
    with transaction(conn):
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def record_store_root(conn: sqlite3.Connection, store_root: Path) -> None:
    """Remember which session store this index describes; a mismatch is what `check` reports."""
    with transaction(conn):
        write_meta(conn, "store_root", str(store_root))


def indexed_store_root(conn: sqlite3.Connection) -> str | None:
    return read_meta(conn, "store_root")


__all__ = [
    "apply_scan",
    "forget_path_owner",
    "forget_session",
    "indexed_store_root",
    "mark_error",
    "mark_missing",
    "mark_ok",
    "mark_stale",
    "mark_unsupported",
    "record_store_root",
    "replace_scan",
    "upsert_session",
]
