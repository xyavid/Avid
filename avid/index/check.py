"""Index check and repair: report what contradicts the files, then apply the recommended fixes.

The sweep is read-only (stat plus in-database consistency), so it is always safe to run; a rewrite
that keeps the file length is only detectable through mtime, which makes it the weakest signal.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..security import userdirs
from . import queries
from .types import INDEX_STATUS_OK, IndexReport
from .writer import forget_session, indexed_store_root

if TYPE_CHECKING:  # pragma: no cover - type checking only, avoids a runtime import cycle
    from .indexer import SessionIndexer

# The three fix actions; None means a human must look first (for example an unknown version).
FIX_FORGET = "forget"
FIX_REBUILD = "rebuild"
FIX_REINDEX = "reindex"


@dataclass(frozen=True)
class Finding:
    """One finding: symptom, detail and the recommended fix (None when no automatic fix exists)."""

    kind: str
    session_id: str
    detail: str
    fix: str | None = None


@dataclass(frozen=True)
class CheckReport:
    findings: tuple[Finding, ...]
    sessions: int
    entries: int
    store_matches: bool
    indexed_store: str | None

    @property
    def healthy(self) -> bool:
        return not self.findings


@dataclass(frozen=True)
class FixTally:
    reindexed: int = 0
    rebuilt: int = 0
    forgotten: int = 0
    skipped: int = 0

    def merged(self, other: "FixTally") -> "FixTally":
        return FixTally(
            reindexed=self.reindexed + other.reindexed,
            rebuilt=self.rebuilt + other.rebuilt,
            forgotten=self.forgotten + other.forgotten,
            skipped=self.skipped + other.skipped,
        )


def check_index(indexer: "SessionIndexer", *, roots: Sequence[Path] | None = None) -> CheckReport:
    """Read-only sweep: what the index believes versus what the files say."""
    scan_roots = [Path(root) for root in (roots if roots is not None else indexer.roots())]
    current_store = str(scan_roots[0]) if scan_roots else str(userdirs.sessions_dir())
    recorded_store = indexed_store_root(indexer.conn)

    findings: list[Finding] = []
    if recorded_store is not None and recorded_store != current_store:
        findings.append(
            Finding(
                "other_store",
                "",
                f"索引描述的是 {recorded_store}，现在看的是 {current_store}",
                FIX_REBUILD,
            )
        )

    rows = queries.list_sessions(indexer.conn)
    known = set()
    for row in rows:
        known.add(row.session_id)
        path = Path(row.file_path)
        if not path.exists():
            findings.append(Finding("missing_file", row.session_id, str(path), FIX_FORGET))
            continue
        try:
            stat = path.stat()
        except OSError as exc:  # pragma: no cover - permissions/race: report it, do not guess
            findings.append(Finding("missing_file", row.session_id, f"{path}（{exc}）", FIX_FORGET))
            continue

        if int(row.indexed_bytes) > stat.st_size:
            findings.append(
                Finding(
                    "cursor_beyond_eof",
                    row.session_id,
                    f"游标 {row.indexed_bytes} > 文件长度 {stat.st_size}",
                    FIX_REBUILD,
                )
            )
        elif int(row.indexed_bytes) < stat.st_size:
            findings.append(
                Finding(
                    "behind",
                    row.session_id,
                    f"还没读到 {stat.st_size - int(row.indexed_bytes)} 字节",
                    FIX_REINDEX,
                )
            )
        elif int(stat.st_mtime * 1000) > int(row.updated_at or 0):
            findings.append(
                Finding(
                    "touched",
                    row.session_id,
                    "文件比索引新，但长度没变（可能是原地改写）",
                    FIX_REBUILD,
                )
            )

        indexed_rows = len(queries.entries_of(indexer.conn, row.session_id))
        if indexed_rows != int(row.entry_count):
            findings.append(
                Finding(
                    "rows_mismatch",
                    row.session_id,
                    f"库里 {indexed_rows} 条，记录里 {row.entry_count} 条",
                    FIX_REBUILD,
                )
            )

        if row.index_status != INDEX_STATUS_OK:
            findings.append(
                Finding(
                    "status",
                    row.session_id,
                    f"{row.index_status}：{row.last_error or '（没有原因）'}",
                    FIX_REINDEX if row.index_status == "error" else None,
                )
            )

    found, notes = indexer.discover()
    for session_id, path in sorted(found.items()):
        if session_id not in known:
            findings.append(Finding("unindexed_file", session_id, str(path), FIX_REINDEX))
    for note in notes:
        findings.append(Finding("unreadable_file", "", note, None))

    stats = queries.index_stats(indexer.conn)
    return CheckReport(
        findings=tuple(findings),
        sessions=int(stats["sessions"]),
        entries=int(stats["entries"]),
        store_matches=recorded_store in (None, current_store),
        indexed_store=recorded_store,
    )


def apply_fixes(indexer: "SessionIndexer", report: CheckReport) -> FixTally:
    """Follow the recommendations; each fix is idempotent, and a fix that cannot be applied counts
    as skipped instead of failing the whole command.
    """
    tally = FixTally()
    for finding in report.findings:
        if finding.fix == FIX_FORGET:
            try:
                forget_session(indexer.conn, finding.session_id)
            except sqlite3.Error:
                tally = tally.merged(FixTally(skipped=1))
                continue
            tally = tally.merged(FixTally(forgotten=1))
        elif finding.fix == FIX_REBUILD:
            if finding.kind == "other_store":
                report_all = indexer.rebuild()
                tally = tally.merged(FixTally(rebuilt=report_all.indexed))
                break  # After a full rebuild the original findings no longer apply.
            try:
                result: IndexReport = indexer.rebuild(finding.session_id)
            except sqlite3.Error:
                tally = tally.merged(FixTally(skipped=1))
                continue
            tally = tally.merged(
                FixTally(rebuilt=1) if result.indexed else FixTally(skipped=1)
            )
        elif finding.fix == FIX_REINDEX:
            ok = indexer.index_session(finding.session_id).indexed
            tally = tally.merged(FixTally(reindexed=1) if ok else FixTally(skipped=1))
        else:
            tally = tally.merged(FixTally(skipped=1))
    return tally


__all__ = [
    "FIX_FORGET",
    "FIX_REBUILD",
    "FIX_REINDEX",
    "CheckReport",
    "Finding",
    "FixTally",
    "apply_fixes",
    "check_index",
]
