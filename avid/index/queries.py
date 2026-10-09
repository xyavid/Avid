"""Read side: session and entry queries plus full-text search; rows carry their byte range and the
caller reads the original text from JSONL.

User input is tokenised and quoted as literals joined by AND, and tokens shorter than 3 characters
take a LIKE scan because the trigram index cannot see them.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .db import row_to_dict
from .types import (
    INDEX_STATUS_OK,
    IndexedSession,
    SearchHit,
)

# Tokens shorter than this skip FTS (trigram granularity) and take a LIKE scan.
MIN_FTS_TOKEN = 3
# Default result count; search is for people, not for export.
DEFAULT_SEARCH_LIMIT = 50
# Snippet window: characters kept on each side of a hit.
SNIPPET_PAD = 60
# What counts as a token: runs of CJK, alphanumerics and underscores.
_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
_SESSION_FIELDS = (
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


def _to_session(row: sqlite3.Row) -> IndexedSession:
    return IndexedSession(**{name: row[name] for name in _SESSION_FIELDS})


def get_session(conn: sqlite3.Connection, session_id: str) -> IndexedSession | None:
    row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    return None if row is None else _to_session(row)


def list_sessions(
    conn: sqlite3.Connection,
    *,
    workspace_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[IndexedSession]:
    """Sessions newest-first, optionally narrowed to one workspace or one index status."""
    clauses: list[str] = []
    params: list[object] = []
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        params.append(workspace_id)
    if status is not None:
        clauses.append("index_status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    tail = ""
    if limit is not None:
        tail = " LIMIT ?"
        params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM sessions {where} ORDER BY updated_at DESC, session_id{tail}", params
    ).fetchall()
    return [_to_session(row) for row in rows]


def count_sessions(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])


def sessions_by_workspace(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT workspace_id, COUNT(*) AS n FROM sessions GROUP BY workspace_id"
    ).fetchall()
    return {(row["workspace_id"] or ""): int(row["n"]) for row in rows}


def sessions_needing_work(conn: sqlite3.Connection) -> list[IndexedSession]:
    """Rows worth a second look: never indexed, failed, or known to be behind."""
    rows = conn.execute(
        "SELECT * FROM sessions WHERE index_status != ? ORDER BY indexed_at IS NULL, indexed_at",
        (INDEX_STATUS_OK,),
    ).fetchall()
    return [_to_session(row) for row in rows]


def rows_missing_from(conn: sqlite3.Connection, known: set[str]) -> list[IndexedSession]:
    """Every indexed session whose id was not in the last discovery pass."""
    rows = conn.execute("SELECT * FROM sessions").fetchall()
    return [_to_session(row) for row in rows if row["session_id"] not in known]


def entries_of(
    conn: sqlite3.Connection, session_id: str, *, limit: int | None = None
) -> list[dict[str, object]]:
    """One session's entries in file order; no text unless asked for (the caller reads JSONL)."""
    tail = "" if limit is None else " LIMIT ?"
    params: list[object] = [session_id] if limit is None else [session_id, limit]
    rows = conn.execute(
        f"""
        SELECT entry_id, seq, type, role, timestamp, byte_offset, byte_length
        FROM entries WHERE session_id = ? ORDER BY seq{tail}
        """,
        params,
    ).fetchall()
    return [row_to_dict(row) or {} for row in rows]


def entry_location(
    conn: sqlite3.Connection, session_id: str, entry_id: str
) -> dict[str, object] | None:
    """Where one entry sits in its file: byte range plus the file path (the jump target)."""
    row = conn.execute(
        """
        SELECT e.entry_id, e.seq, e.byte_offset, e.byte_length, s.file_path
        FROM entries e JOIN sessions s ON s.session_id = e.session_id
        WHERE e.session_id = ? AND e.entry_id = ?
        """,
        (session_id, entry_id),
    ).fetchone()
    return row_to_dict(row)


def tokens(query: str) -> list[str]:
    """Split user input into literal tokens; everything that is not a word character is a separator."""
    return [match.group(0) for match in _TOKEN.finditer(query)]


def _snippet(text: str, needles: Sequence[str]) -> str:
    """A window around the first hit, so a result reads like a quote and not like a database row."""
    if not text:
        return ""
    lowered = text.lower()
    at = len(text)
    for needle in needles:
        found = lowered.find(needle.lower())
        if found != -1:
            at = min(at, found)
    if at == len(text):
        return " ".join(text[: SNIPPET_PAD * 2].split())
    start = max(0, at - SNIPPET_PAD)
    end = min(len(text), at + SNIPPET_PAD)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + " ".join(text[start:end].split()) + suffix


def _like_pattern(token: str) -> str:
    """Escape LIKE wildcards so a query containing % or _ searches for those characters."""
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fetch_hits(
    conn: sqlite3.Connection,
    *,
    where: str,
    params: list[object],
    needles: Sequence[str],
    limit: int,
) -> list[SearchHit]:
    rows = conn.execute(
        f"""
        SELECT e.session_id, e.entry_id, e.seq, e.type, e.role, e.timestamp,
               e.byte_offset, e.byte_length, e.search_text,
               s.title, s.workspace_id, s.workspace_name, s.updated_at
        FROM entries e JOIN sessions s ON s.session_id = e.session_id
        WHERE {where}
        ORDER BY s.updated_at DESC, e.seq DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    hits: list[SearchHit] = []
    for row in rows:
        hits.append(
            SearchHit(
                session_id=row["session_id"],
                entry_id=row["entry_id"],
                seq=int(row["seq"]),
                entry_type=row["type"],
                role=row["role"],
                timestamp=row["timestamp"],
                byte_offset=int(row["byte_offset"]),
                byte_length=int(row["byte_length"]),
                snippet=_snippet(str(row["search_text"] or ""), needles),
                title=row["title"],
                workspace_id=row["workspace_id"],
                workspace_name=row["workspace_name"],
            )
        )
    return hits


def search_entries(
    conn: sqlite3.Connection,
    query: str,
    *,
    session_id: str | None = None,
    workspace_id: str | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> list[SearchHit]:
    """Search entry text; long tokens go through FTS5, short ones through a LIKE scan."""
    words = tokens(query)
    if not words:
        return []
    scope: list[str] = []
    scope_params: list[object] = []
    if session_id is not None:
        scope.append("e.session_id = ?")
        scope_params.append(session_id)
    if workspace_id is not None:
        scope.append("s.workspace_id = ?")
        scope_params.append(workspace_id)

    long_words = [word for word in words if len(word) >= MIN_FTS_TOKEN]
    short_words = [word for word in words if len(word) < MIN_FTS_TOKEN]

    hits: list[SearchHit] = []
    if short_words:
        # A short token sends every word through LIKE; merging an FTS pass breaks the AND contract.
        clauses = [" AND ".join("e.search_text LIKE ? ESCAPE '\\'" for _ in words)]
        params: list[object] = [_like_pattern(word) for word in words]
        clauses.extend(scope)
        hits = _fetch_hits(
            conn, where=" AND ".join(clauses), params=params, needles=words, limit=limit
        )
    elif long_words:
        # Quoting makes each word a literal: AND/OR is not parsed and * is not a wildcard.
        expression = " AND ".join(f'"{word}"' for word in long_words)
        where = ["e.entry_pk IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?)"]
        params = [expression, *scope_params]
        where.extend(scope)
        hits = _fetch_hits(
            conn,
            where=" AND ".join(where),
            params=params,
            needles=long_words,
            limit=limit,
        )

    return hits[:limit]


def index_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Cheap facts for the CLI/`check`: rows, statuses and cursor lag."""
    sessions = count_sessions(conn)
    entries = int(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
    statuses = {
        str(row["index_status"]): int(row["n"])
        for row in conn.execute(
            "SELECT index_status, COUNT(*) AS n FROM sessions GROUP BY index_status"
        )
    }
    # "Behind" = cursor short of EOF or the file gone; both make search results untrustworthy.
    behind = 0
    for row in conn.execute(
        "SELECT file_path, file_size, indexed_bytes FROM sessions WHERE index_status = ?",
        (INDEX_STATUS_OK,),
    ):
        if int(row["indexed_bytes"]) < int(row["file_size"]) or not Path(row["file_path"]).exists():
            behind += 1
    return {
        "sessions": sessions,
        "entries": entries,
        "statuses": statuses,
        "behind": behind,
    }


__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MIN_FTS_TOKEN",
    "count_sessions",
    "entries_of",
    "entry_location",
    "get_session",
    "index_stats",
    "list_sessions",
    "rows_missing_from",
    "search_entries",
    "sessions_by_workspace",
    "sessions_needing_work",
    "tokens",
]
