"""读侧：会话与条目的查询，以及全文检索。

这一层不写任何东西，也不猜：查到的每一行都带着它在文件里的位置（字节区间），
调用方要原文就回 JSONL 取。列表类查询默认按最近更新倒序——「找刚才那个会话」
是这里最常见的用法。

FTS5 查询语法在这里收口：用户输入里可能有 `AND`、引号、`*`、括号，直接塞进 MATCH
会得到语法错误甚至非预期的表达式。策略是**把词当成词**：拆成词元、逐个加引号、
词之间按 AND 连接；`<3` 字符的词元走 LIKE 扫描（trigram 索引按 3 字符成组，短词它
匹配不到，而这个代价在几千行的量级上是毫秒）。
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from .db import row_to_dict
from .types import (
    INDEX_STATUS_OK,
    IndexedSession,
    SearchHit,
)

# 短于这个长度的词元不进 FTS（trigram 的粒度），改走 LIKE 扫描。
MIN_FTS_TOKEN = 3
# 默认返回条数；搜索是给人看的，不是导出。
DEFAULT_SEARCH_LIMIT = 50
# 片段窗口：命中词两侧各留这些字符。
SNIPPET_PAD = 60
# 什么算一个词元：CJK、字母数字与下划线连续段。
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
    if limit is not None:
        params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM sessions {where} ORDER BY updated_at DESC, session_id", params
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
    if long_words:
        # 加引号 = 当成字面量：既不解析 AND/OR，也不会把 * 当通配符。
        expression = " AND ".join(f'"{word}"' for word in long_words)
        where = ["e.entry_pk IN (SELECT rowid FROM entries_fts WHERE entries_fts MATCH ?)"]
        params: list[object] = [expression, *scope_params]
        where.extend(scope)
        hits = _fetch_hits(
            conn,
            where=" AND ".join(where),
            params=params,
            needles=[*long_words, *short_words],
            limit=limit,
        )

    if short_words:
        # 短词（中文两字词最常见）FTS 看不见：直接扫 search_text。几千行的量级是毫秒级，
        # 所以不设扫描上限，也不假装结果不全——真慢了是加索引的信号，不是这里加闸门。
        clauses = [" AND ".join("e.search_text LIKE ? ESCAPE '\\'" for _ in short_words)]
        params = [_like_pattern(word) for word in short_words]
        clauses.extend(scope)
        seen = {(hit.session_id, hit.entry_id) for hit in hits}
        for hit in _fetch_hits(
            conn,
            where=" AND ".join(clauses),
            params=params,
            needles=words,
            limit=limit,
        ):
            if (hit.session_id, hit.entry_id) not in seen:
                hits.append(hit)

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
    behind = int(
        conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE indexed_bytes < file_size AND index_status = ?",
            (INDEX_STATUS_OK,),
        ).fetchone()[0]
    )
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
