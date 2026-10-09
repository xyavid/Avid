"""Index DB: connection setup, migrations, lock behavior, and rebuilding after deletion."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from avid.index import INDEX_STATUSES
from avid.index import db as index_db
from avid.security import userdirs


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_open_creates_the_database_and_the_current_schema(tmp_path):
    conn = index_db.open_db(tmp_path / "sessions.sqlite")
    try:
        assert index_db.schema_version(conn) == index_db.SCHEMA_VERSION
        assert {"meta", "sessions", "entries"} <= table_names(conn)
    finally:
        conn.close()


def test_table_names_are_not_shadowed_by_sqlite_internals(tmp_path):
    """The entries PK is ``entry_pk``, not ``rowid``: the implicit rowid would mispoint the FTS
    external-content mode."""
    conn = index_db.open_db(tmp_path / "sessions.sqlite")
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(entries)")}
        assert "entry_pk" in columns and "rowid" not in columns
    finally:
        conn.close()


def test_reopening_is_idempotent_and_keeps_data(tmp_path):
    path = tmp_path / "sessions.sqlite"
    conn = index_db.open_db(path)
    index_db.write_meta(conn, "store_root", "/some/where")
    conn.close()

    again = index_db.open_db(path)
    try:
        assert index_db.schema_version(again) == index_db.SCHEMA_VERSION
        assert index_db.read_meta(again, "store_root") == "/some/where"
    finally:
        again.close()


def test_migrations_apply_in_order(tmp_path):
    """An old DB gets only the missing migration steps (user_version is SQLite's version slot)."""
    conn = index_db.open_db(tmp_path / "sessions.sqlite", migrations=index_db.MIGRATIONS[:1])
    assert index_db.schema_version(conn) == index_db.MIGRATIONS[0][0]
    assert "entries" not in table_names(conn)

    applied = index_db.migrate(conn)

    assert applied == index_db.SCHEMA_VERSION
    assert "entries" in table_names(conn)


def test_a_locked_database_gives_up_quickly_instead_of_hanging(tmp_path):
    """The index gives up on a lock instead of hanging: it must never stall a run thread."""
    path = tmp_path / "sessions.sqlite"
    holder = index_db.open_db(path)
    writer = index_db.open_db(path, timeout_ms=200)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError) as exc:
            writer.execute("INSERT INTO meta(key, value) VALUES ('x', 'y')")
        elapsed = time.monotonic() - started

        assert "locked" in str(exc.value).lower()
        assert elapsed < 2.0, elapsed
    finally:
        holder.execute("ROLLBACK")
        holder.close()
        writer.close()


def test_wal_and_foreign_keys_are_on(tmp_path):
    conn = index_db.open_db(tmp_path / "sessions.sqlite")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_deleting_the_database_starts_from_scratch(tmp_path):
    path = tmp_path / "sessions.sqlite"
    conn = index_db.open_db(path)
    index_db.write_meta(conn, "store_root", "/gone")
    conn.close()
    path.unlink()

    rebuilt = index_db.open_db(path)
    try:
        assert index_db.schema_version(rebuilt) == index_db.SCHEMA_VERSION
        assert index_db.read_meta(rebuilt, "store_root") is None
    finally:
        rebuilt.close()


def test_the_index_lives_under_the_avid_home_and_can_be_moved(tmp_path, monkeypatch):
    home = userdirs.avid_home()
    assert userdirs.index_path() == home / "index" / "sessions.sqlite"

    monkeypatch.setenv(userdirs.INDEX_DIR_ENV, str(tmp_path / "elsewhere"))
    assert userdirs.index_path() == tmp_path / "elsewhere" / "sessions.sqlite"
    assert userdirs.index_dir_source() == "env"


def test_open_db_defaults_to_the_index_path(monkeypatch, tmp_path):
    monkeypatch.setenv(userdirs.INDEX_DIR_ENV, str(tmp_path / "indexed"))
    conn = index_db.open_db()
    try:
        assert index_db.schema_version(conn) == index_db.SCHEMA_VERSION
    finally:
        conn.close()
    assert (tmp_path / "indexed" / "sessions.sqlite").exists()


def test_a_directory_that_cannot_be_created_is_an_error_not_a_crash(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("我是个文件，不是目录\n", encoding="utf-8")

    with pytest.raises(OSError):
        index_db.open_db(blocker / "sessions.sqlite")


def test_meta_round_trips_missing_keys_as_none(tmp_path):
    conn = index_db.open_db(tmp_path / "sessions.sqlite")
    try:
        assert index_db.read_meta(conn, "nope") is None
        index_db.write_meta(conn, "a", "1")
        index_db.write_meta(conn, "a", "2")
        assert index_db.read_meta(conn, "a") == "2"
    finally:
        conn.close()


def test_index_status_names_are_stable(tmp_path):
    """Status names are stored strings; changing one is a data migration, so pin them here."""
    assert set(INDEX_STATUSES) == {
        "ok",
        "pending",
        "stale",
        "error",
        "missing",
        "unsupported",
    }


def test_path_types_are_accepted(tmp_path):
    conn = index_db.open_db(Path(tmp_path) / "sub" / "sessions.sqlite")
    try:
        assert index_db.schema_version(conn) == index_db.SCHEMA_VERSION
    finally:
        conn.close()
