"""SQLite 连接与 schema 迁移：索引库是派生层，删掉就能从 JSONL 重建。

三条纪律写在这里，后面每一步都照它办：

- **user_version 是版本位**：不另建版本表（少一份要维护的真相），每次打开只补差的那几步；
- **迁移是原子的**：脚本自带 BEGIN/COMMIT，半途失败不会留下半个 schema；
- **等不到锁就认输**：`busy_timeout` 到期直接抛 OperationalError——索引落后没关系，
  把运行线程拖住才是事故。
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

# 2 秒：本地单用户工具里超过这个时间还没拿到锁，说明有别的东西卡住了（等下去只会更糟）。
BUSY_TIMEOUT_MS = 2000
# Key in `meta` holding the session store this index was built from; a mismatch is a check finding.
META_STORE_ROOT = "store_root"
META_BUILT_BY = "built_by"

# schema 的每一步；列表本身就是历史，只能往后追加。
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
    """打开（必要时建好）索引库并把 schema 补到最新。"""
    target = Path(path) if path is not None else userdirs.index_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(target, timeout_ms)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError as exc:
        # 「不是数据库」= 文件坏了：它是可丢的派生层，挪到一边重建比让整个进程起不来强。
        if not _looks_corrupt(exc):
            conn.close()
            raise
        conn.close()
        aside = target.with_name(f"{target.name}.corrupt-{int(time.time())}")
        logger.warning("索引库不是数据库，挪到一边重建：%s → %s", target, aside)
        os.replace(target, aside)
        for suffix in ("-wal", "-shm"):  # WAL 的伴生文件一起挪，别让新库继承它们
            with contextlib.suppress(OSError):
                os.replace(Path(str(target) + suffix), Path(str(aside) + suffix))
        conn = _connect(target, timeout_ms)
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {timeout_ms}")
    # 派生层：崩了重建即可，不值得为它付每次提交的 fsync。
    conn.execute("PRAGMA synchronous = NORMAL")
    migrate(conn, migrations=migrations)
    return conn


def _connect(target: Path, timeout_ms: int) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(target),
        timeout=timeout_ms / 1000,
        isolation_level=None,  # 事务由 transaction() 显式开，别让驱动替我们决定
        check_same_thread=False,  # 索引线程与请求线程共用一个连接，串行化由调用方保证
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
    """按版本号补差；返回补完后的版本。"""
    current = schema_version(conn)
    for version, script in migrations:
        if version <= current:
            continue
        # executescript 会先隐式提交，所以事务边界写进脚本本身——一次迁移要么整段生效，要么整段没发生。
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        current = version
    return current


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT；任何异常都回滚，写者之间不会看到半个批次。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        # COMMIT 自己失败（盘满/等不到锁）也要回滚：否则事务一直开着，
        # 之后每一次 BEGIN IMMEDIATE 都报「cannot start a transaction within a transaction」。
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
