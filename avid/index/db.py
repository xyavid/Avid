"""SQLite connection and schema migrations for the derived index, which can be deleted and
rebuilt from JSONL.

`user_version` is the version marker, each migration script carries its own BEGIN/COMMIT so it lands
atomically, and a lock that outlives `busy_timeout` raises instead of stalling the run.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from ..security import userdirs

logger = logging.getLogger("avid.index.db")

# Two seconds: past this a local single-user tool is stuck elsewhere, and waiting only hurts.
BUSY_TIMEOUT_MS = 2000
# Key in `meta` holding the session store this index was built from; a mismatch is a check finding.
META_STORE_ROOT = "store_root"
META_BUILT_BY = "built_by"

# Every schema step in order; the list is append-only because it is the version history.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id      TEXT PRIMARY KEY,
            file_path       TEXT NOT NULL UNIQUE,
            workspace_id    TEXT,
            workspace_root  TEXT,
            workspace_name  TEXT,
            title           TEXT,
            first_user_text TEXT,
            created_at      INTEGER,
            updated_at      INTEGER,
            file_size       INTEGER NOT NULL DEFAULT 0,
            indexed_bytes   INTEGER NOT NULL DEFAULT 0,
            entry_count     INTEGER NOT NULL DEFAULT 0,
            indexed_at      INTEGER,
            index_status    TEXT NOT NULL DEFAULT 'pending',
            last_error      TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated
            ON sessions(workspace_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(index_status);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS entries (
            entry_pk    INTEGER PRIMARY KEY,
            session_id  TEXT NOT NULL,
            entry_id    TEXT NOT NULL,
            seq         INTEGER NOT NULL,
            type        TEXT NOT NULL,
            role        TEXT,
            timestamp   INTEGER,
            byte_offset INTEGER NOT NULL,
            byte_length INTEGER NOT NULL,
            search_text TEXT NOT NULL DEFAULT '',
            UNIQUE(session_id, entry_id),
            UNIQUE(session_id, seq),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_entries_session_seq ON entries(session_id, seq);
        """,
    ),
    (
        3,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            search_text,
            content='entries',
            content_rowid='entry_pk',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS entries_fts_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, search_text) VALUES (new.entry_pk, new.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_fts_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, search_text)
            VALUES ('delete', old.entry_pk, old.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS entries_fts_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, search_text)
            VALUES ('delete', old.entry_pk, old.search_text);
            INSERT INTO entries_fts(rowid, search_text) VALUES (new.entry_pk, new.search_text);
        END;
        """,
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1][0]


def open_db(
    path: str | Path | None = None,
    *,
    timeout_ms: int = BUSY_TIMEOUT_MS,
    migrations: Sequence[tuple[int, str]] = MIGRATIONS,
) -> sqlite3.Connection:
    """Open (creating when needed) the index database and bring its schema up to date."""
    target = Path(path) if path is not None else userdirs.index_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(target, timeout_ms)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        # "Not a database" = corrupt: move it aside and rebuild, since this layer is disposable.
        if not _looks_corrupt(exc):
            conn.close()
            raise
        conn.close()
        aside = target.with_name(f"{target.name}.corrupt-{int(time.time())}")
        logger.warning("索引库不是数据库，挪到一边重建：%s → %s", target, aside)
        os.replace(target, aside)
        for suffix in ("-wal", "-shm"):  # Move WAL sidecars too, or the new database inherits them
            with contextlib.suppress(OSError):
                os.replace(Path(str(target) + suffix), Path(str(aside) + suffix))
        conn = _connect(target, timeout_ms)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    # Derived layer: a crash means rebuild, so it is not worth a per-commit fsync.
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn, migrations=migrations)
    return conn


def _connect(target: Path, timeout_ms: int) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(target),
        timeout=timeout_ms / 1000,
        isolation_level=None,  # transaction() opens transactions explicitly
        check_same_thread=False,  # shared by index and request threads; caller serialises
    )
    conn.row_factory = sqlite3.Row
    return conn


def _looks_corrupt(exc: sqlite3.DatabaseError) -> bool:
    """True for the two messages SQLite uses when the file simply is not a database."""
    text = str(exc).lower()
    return "not a database" in text or "file is encrypted" in text


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(
    conn: sqlite3.Connection, *, migrations: Sequence[tuple[int, str]] = MIGRATIONS
) -> int:
    """Apply the missing migrations in version order and return the resulting version."""
    current = schema_version(conn)
    for version, script in migrations:
        if version <= current:
            continue
        # executescript commits implicitly, so each script carries its transaction: all or none.
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        current = version
    return current


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Open with BEGIN IMMEDIATE and COMMIT at the end; any exception rolls back, so writers never
    see half a batch.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        # A failed COMMIT (disk full, lock timeout) must also roll back, or every later BEGIN fails.
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def read_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def write_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Row → dict for the wire; sqlite3.Row already speaks the mapping protocol."""
    return None if row is None else dict(row)


__all__ = [
    "BUSY_TIMEOUT_MS",
    "META_BUILT_BY",
    "META_STORE_ROOT",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "migrate",
    "open_db",
    "read_meta",
    "row_to_dict",
    "schema_version",
    "transaction",
    "write_meta",
]
