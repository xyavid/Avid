"""Indexer: discover session files, process each from its cursor, and reconcile or rebuild
on demand.

Notifications come from the assembly layer and never drag the run layer in; one connection runs one
pass at a time; failures only leave the index behind (`last_error`), while workspace ownership comes
from the file header and the directory name is only a fallback.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..security import userdirs
from ..session import now_ms
from ..session.errors import SessionStorageError
from .db import open_db
from .queries import get_session, rows_missing_from, sessions_needing_work
from .scanner import read_session_facts, scan_file
from .types import (
    INDEX_STATUS_OK,
    IndexedSession,
    IndexReport,
    ScanResult,
)
from .writer import (
    apply_scan,
    forget_path_owner,
    forget_session,
    mark_error,
    mark_missing,
    mark_unsupported,
    record_store_root,
    replace_scan,
)

logger = logging.getLogger("avid.index.indexer")

# Path prefix of the per-workspace subdirectories (derive_id's product); used only as a fallback.
WORKSPACE_DIR_PREFIX = "w-"
# How many per-session failures a report spells out before it just counts them.
MAX_REPORTED_DETAILS = 10
# Settle window: one run commits several messages in a row, so waiting briefly merges them.
NOTIFY_SETTLE_SECONDS = 0.2
# Upper bound on draining the queue; timing out is not an error, it just means still behind.
DEFAULT_FLUSH_SECONDS = 2.0
# Workspace-name cache window: display facts change, but a registry read per session is too much.
WORKSPACE_LOOKUP_TTL_SECONDS = 5.0


def workspace_id_from_path(path: Path) -> str | None:
    """Fallback ownership: the parent directory name when it looks like a workspace id."""
    parent = path.parent.name
    return parent if parent.startswith(WORKSPACE_DIR_PREFIX) else None


class SessionIndexer:
    """One process's index writer: a single connection guarded by a lock, one pass at a time."""

    def __init__(
        self,
        *,
        conn: sqlite3.Connection | None = None,
        db_path: str | Path | None = None,
        roots: Callable[[], Sequence[Path]] | None = None,
        lookup_workspace: Callable[[str], tuple[str | None, str | None] | None] | None = None,
        now: Callable[[], int] = now_ms,
        owns_conn: bool | None = None,
    ) -> None:
        self._conn = conn if conn is not None else open_db(db_path)
        # Borrowed connections (tests, callers) are not ours to close; we close only what we opened.
        self._owns_conn = conn is None if owns_conn is None else owns_conn
        self._lock = threading.RLock()
        self._roots = roots or (lambda: [userdirs.sessions_dir()])
        self._lookup_workspace = lookup_workspace
        self._now = now
        # Notification queue: a set merges per session, one pass per batch, _busy lets flush wait.
        self._queue = threading.Condition()
        self._pending: set[str] = set()
        self._busy = False
        self._worker: threading.Thread | None = None
        self._stopped = False
        self._bootstrap = False
        # Pass mutex: catch-up and check/rebuild share one connection, so never run at once.
        self._pass_lock = threading.Lock()

    # Lifecycle.

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        self.stop(flush=False)
        with self._lock:
            if self._owns_conn:
                self._conn.close()
                self._owns_conn = False

    # Notification: the assembly layer says "this session has new rows".
    #
    # Enqueue only: next reconcile catches lost notifications, and indexing must never stall a run.

    def notify(self, session_id: str) -> None:
        self.start(initial_reconcile=False)
        with self._queue:
            self._pending.add(session_id)
            self._queue.notify_all()

    def start(self, *, initial_reconcile: bool = True) -> None:
        """Start the background worker; with ``initial_reconcile`` it catches up missed work first."""
        with self._queue:
            if initial_reconcile:
                self._bootstrap = True
            if self._worker is not None:
                self._queue.notify_all()
                return
            self._stopped = False
            self._worker = threading.Thread(target=self._work, name="avid-index", daemon=True)
            self._worker.start()

    def flush(self, timeout: float = DEFAULT_FLUSH_SECONDS) -> bool:
        """Wait for the queue to drain; False means it is still behind (never an error)."""
        deadline = time.monotonic() + timeout
        with self._queue:
            while self._pending or self._busy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.wait(remaining)
            return True

    def stop(self, *, flush: bool = True, timeout: float = DEFAULT_FLUSH_SECONDS) -> None:
        """Drain (unless told not to) and stop the worker; safe to call more than once."""
        if flush:
            self.flush(timeout)
        with self._queue:
            worker = self._worker
            self._stopped = True
            self._queue.notify_all()
        if worker is not None:
            worker.join(timeout)
            if worker.is_alive():
                # Still running: keep the reference, or a later start() spawns a second worker.
                logger.warning("索引线程还在收尾，先把引用留着；下一次 start 会复用它")
                return
        with self._queue:
            if self._worker is worker:
                self._worker = None

    def _record_store(self) -> None:
        """Remember which session store this index describes; `check` compares it on the next run."""
        roots = self.roots()
        with self._lock:
            record_store_root(self._conn, roots[0] if roots else userdirs.sessions_dir())

    def _is_stopped(self) -> bool:
        """Read the stop flag through a call: another thread sets it, so mypy must not narrow it."""
        with self._queue:
            return self._stopped

    def pending(self) -> int:
        """How many sessions are waiting for a pass; the CLI prints it after a run."""
        with self._queue:
            return len(self._pending)

    def _work(self) -> None:
        """The worker loop: one catch-up pass first, then drain whatever gets notified."""
        with self._queue:
            bootstrap, self._bootstrap = self._bootstrap, False
        if bootstrap:
            try:
                self.reconcile()
            except (sqlite3.Error, OSError) as exc:
                logger.warning("启动补齐没能跑完（索引落后没关系）：%s", exc)
        while True:
            with self._queue:
                stopped = self._is_stopped()
                if stopped:
                    return
                if not self._pending:
                    self._queue.wait(NOTIFY_SETTLE_SECONDS)
                    if self._is_stopped():
                        return
                    if not self._pending:
                        continue
                # Settle window: notifications arriving here join the same pass.
                self._queue.wait(NOTIFY_SETTLE_SECONDS)
                batch = sorted(self._pending)
                self._pending.clear()
                self._busy = True
            try:
                with self._pass_lock:
                    self._drain(batch)
            except (sqlite3.Error, OSError) as exc:
                logger.warning("索引这一遍失败（索引落后没关系）：%s", exc)
            finally:
                with self._queue:
                    self._busy = False
                    self._queue.notify_all()

    def _drain(self, session_ids: Sequence[str]) -> None:
        """Index the given sessions, newest rows only; unknown ids are looked up once per batch."""
        self._record_store()
        discovered: dict[str, Path] | None = None
        for session_id in session_ids:
            with self._lock:
                row = get_session(self._conn, session_id)
            if row is not None:
                path: Path | None = Path(row.file_path)
            else:
                if discovered is None:
                    discovered, _ = self.discover()
                path = discovered.get(session_id)
            if path is None or not path.exists():
                # Path gone (rename/migrate/delete): rediscover or forget the row, no ghost hits.
                if discovered is None:
                    discovered, _ = self.discover()
                path = discovered.get(session_id)
                if path is None:
                    if row is not None:
                        with self._lock:
                            forget_session(self._conn, session_id)
                    continue
            self._index_one_reported(session_id, path, force_full=False)

    # Discovery.

    def roots(self) -> list[Path]:
        return [Path(root) for root in self._roots()]

    def discover(self) -> tuple[dict[str, Path], tuple[str, ...]]:
        """Every session file under the roots: ``{session_id: path}`` plus unreadable-file notes.

        Store layout is ``<root>/<workspace id>/*.jsonl``; a flat ``<root>/*.jsonl`` is accepted
        too, because that is what a caller passing a store path directly gets.
        """
        found: dict[str, Path] = {}
        notes: list[str] = []
        for root in self.roots():
            if not root.is_dir():
                continue
            directories = [root, *(item for item in sorted(root.glob("*")) if item.is_dir())]
            for directory in directories:
                for path in sorted(directory.glob("*.jsonl")):
                    try:
                        session_id, _, _, unsupported = read_session_facts(path)
                    except SessionStorageError as exc:
                        notes.append(f"{path}：{exc}")
                        continue
                    if unsupported:
                        # Record it: an unsupported row is easier to investigate than silence.
                        notes.append(f"{path}：不认识的 {unsupported}")
                    found.setdefault(session_id, path)
        return found, tuple(notes)

    # Indexing.

    def index_file(self, path: Path, *, force_full: bool = False) -> bool:
        """Index one session file; returns True when the pass wrote something."""
        path = Path(path)
        try:
            session_id, _, _, unsupported = read_session_facts(path)
        except SessionStorageError as exc:
            logger.info("跳过读不了的文件 %s：%s", path, exc)
            return False
        if unsupported:
            with self._lock:
                mark_unsupported(self._conn, session_id, unsupported, file_path=str(path))
            return False
        return self._index_one(session_id, path, force_full=force_full)

    def index_session(self, session_id: str) -> IndexReport:
        """Index one known session by id, using the path recorded in the index."""
        self._record_store()
        with self._lock:
            row = get_session(self._conn, session_id)
        if row is None:
            found, _ = self.discover()
            path = found.get(session_id)
            if path is None:
                return IndexReport(skipped=1, details=(f"{session_id}：索引与文件里都没有",))
        else:
            path = Path(row.file_path)
        ok = self._index_one(session_id, path, force_full=False)
        return IndexReport(indexed=1 if ok else 0, failed=0 if ok else 1)

    def index_all(self) -> IndexReport:
        """Discover every session file and bring each one up to date."""
        with self._pass_lock:
            return self._index_all_locked()

    def _index_all_locked(self) -> IndexReport:
        found, notes = self.discover()
        report = IndexReport(details=notes)
        self._record_store()
        for session_id, path in sorted(found.items()):
            report = report.merged(self._index_one_reported(session_id, path, force_full=False))
            if report.details and report.details[-1].startswith("索引库不可用"):
                break  # Database unusable: retrying only waits for a lock that never comes.
        return report

    def reconcile(self) -> IndexReport:
        """Startup catch-up: new files, stale cursors, and rows whose files are gone."""
        with self._pass_lock:
            return self._reconcile_locked()

    def _reconcile_locked(self) -> IndexReport:
        found, notes = self.discover()
        report = IndexReport(details=notes)
        self._record_store()
        with self._lock:
            stale = sessions_needing_work(self._conn)

        targets = dict(found)
        for row in stale:
            targets.setdefault(row.session_id, Path(row.file_path))

        for session_id, path in sorted(targets.items()):
            if not path.exists():
                with self._lock:
                    if get_session(self._conn, session_id) is not None:
                        mark_missing(self._conn, session_id)
                report = report.merged(IndexReport(skipped=1))
                continue
            outcome = self._index_one_reported(session_id, path, force_full=False)
            report = report.merged(outcome)
            if outcome.details and outcome.details[-1].startswith("索引库不可用"):
                break

        # Rows under the scanned roots whose files no longer exist: the session file was deleted.
        with self._lock:
            for row in rows_missing_from(self._conn, set(found)):
                path = Path(row.file_path)
                if not path.exists() and any(_under(path, root) for root in self.roots()):
                    forget_session(self._conn, row.session_id)
                    report = report.merged(IndexReport(removed=1))
        return report

    def rebuild(self, session_id: str | None = None) -> IndexReport:
        """Re-read session files from byte zero; one id, or everything when none is given."""
        with self._pass_lock:
            return self._rebuild_locked(session_id)

    def _rebuild_locked(self, session_id: str | None) -> IndexReport:
        if session_id is not None:
            with self._lock:
                row = get_session(self._conn, session_id)
            if row is None:
                found, _ = self.discover()
                path = found.get(session_id)
                if path is None:
                    return IndexReport(skipped=1, details=(f"{session_id}：没有这个会话",))
            else:
                path = Path(row.file_path)
            ok = self._index_one(session_id, path, force_full=True)
            return IndexReport(indexed=1 if ok else 0, failed=0 if ok else 1)

        found, notes = self.discover()
        report = IndexReport(details=notes)
        self._record_store()
        for session_id, path in sorted(found.items()):
            outcome = self._index_one_reported(session_id, path, force_full=True)
            report = report.merged(outcome)
            if outcome.details and outcome.details[-1].startswith("索引库不可用"):
                break
        return report

    # One session, from its cursor to the end of the file.

    def _index_one_reported(self, session_id: str, path: Path, *, force_full: bool) -> IndexReport:
        """One session's pass, with the two failure kinds kept apart.

        A constraint error belongs to that session (bad file, duplicate id): report it and keep
        going. Anything else means the database itself is unusable — those must stop the pass,
        because every later session would just wait for a lock that is not coming.
        """
        try:
            ok = self._index_one(session_id, path, force_full=force_full)
        except sqlite3.IntegrityError as exc:
            logger.warning("索引 %s 撞上约束错误（跳过它，继续别的）：%s", path, exc)
            with contextlib.suppress(sqlite3.Error):
                with self._lock:
                    mark_error(self._conn, session_id, file_path=str(path), error=str(exc))
            return IndexReport(failed=1, details=(f"{session_id}：{exc}",))
        except sqlite3.Error as exc:
            return IndexReport(failed=1, details=(f"索引库不可用，中止这一遍：{exc}",))
        return IndexReport(indexed=1 if ok else 0, skipped=0 if ok else 1)

    def _index_one(self, session_id: str, path: Path, *, force_full: bool) -> bool:
        with self._lock:
            row = get_session(self._conn, session_id)
        start = 0 if force_full or row is None else int(row.indexed_bytes)
        previous = None if start == 0 or row is None else _previous_from(row)

        try:
            scan = scan_file(path, start_offset=start, previous=previous)
        except SessionStorageError as exc:
            if not force_full and "游标越界" in str(exc):
                # File shorter than the cursor: truncated or replaced, so rebuild; do not guess.
                return self._index_one(session_id, path, force_full=True)
            with self._lock:
                mark_error(self._conn, session_id, file_path=str(path), error=str(exc))
            logger.warning("索引 %s 失败：%s", path, exc)
            return False

        if scan.unsupported is not None:
            with self._lock:
                mark_unsupported(self._conn, session_id, scan.unsupported, file_path=str(path))
            return False
        if scan.session_id is None:
            with self._lock:
                mark_error(self._conn, session_id, file_path=str(path), error="文件没有 header")
            return False

        workspace_id = scan.workspace_id or workspace_id_from_path(path)
        root, name = self._workspace(workspace_id)
        try:
            stat = path.stat()
        except OSError as exc:
            with self._lock:
                mark_missing(self._conn, session_id)
            logger.info("文件不在了 %s：%s", path, exc)
            return False

        # Same length, newer mtime = in-place rewrite; re-scan fully, or check loses the evidence.
        if (
            row is not None
            and not force_full
            and not scan.entries
            and int(row.indexed_bytes) == scan.next_offset
            and int(row.file_size) == stat.st_size
            and int(row.updated_at or 0) != int(stat.st_mtime * 1000)
            and row.index_status == INDEX_STATUS_OK
        ):
            return self._index_one(session_id, path, force_full=True)

        # No new entries, same size, same mtime, same path, still ok: nothing to write this pass.
        if (
            not scan.entries
            and row is not None
            and not force_full
            and int(row.indexed_bytes) == scan.next_offset
            and int(row.file_size) == stat.st_size
            and int(row.updated_at or 0) == int(stat.st_mtime * 1000)
            and str(row.file_path) == str(path)
            and row.index_status == INDEX_STATUS_OK
        ):
            return False

        session = IndexedSession(
            session_id=scan.session_id,
            file_path=str(path),
            workspace_id=workspace_id,
            workspace_root=root,
            workspace_name=name,
            title=scan.title,
            first_user_text=scan.first_user_text,
            created_at=scan.created_at,
            updated_at=int(stat.st_mtime * 1000),
            file_size=stat.st_size,
            indexed_bytes=scan.next_offset,
            entry_count=scan.entry_count,
            indexed_at=self._now(),
            index_status=INDEX_STATUS_OK,
            last_error=None,
        )
        with self._lock:
            # UNIQUE file_path: another id's row must yield first, or the insert fails every pass.
            forget_path_owner(self._conn, str(path), scan.session_id)
            if scan.session_id != session_id:
                # The file now belongs to another session (copy or rename): drop the old id's row.
                forget_session(self._conn, session_id)
            if force_full:
                replace_scan(self._conn, session, scan)
            else:
                apply_scan(self._conn, session, scan)
        return True

    def _workspace(self, workspace_id: str | None) -> tuple[str | None, str | None]:
        """(root, name) for a workspace id, from the lookup the caller injected; None when unknown."""
        if workspace_id is None or self._lookup_workspace is None:
            return None, None
        found = self._lookup_workspace(workspace_id)
        return (None, None) if found is None else found


def workspace_lookup(
    loader: Callable[[], Iterable[tuple[str, str | None, str | None]]],
    *,
    ttl: float = WORKSPACE_LOOKUP_TTL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[str], tuple[str | None, str | None] | None]:
    """Build ``id -> (root, name)`` from a loader, refreshed after a short window so a renamed
    workspace cannot show a stale name forever.
    """
    table: dict[str, tuple[str | None, str | None]] = {}
    loaded_at = float("-inf")

    def lookup(workspace_id: str) -> tuple[str | None, str | None] | None:
        nonlocal loaded_at
        now = clock()
        if now - loaded_at > ttl:
            table.clear()
            for item_id, root, name in loader():
                table[item_id] = (root, name)
            loaded_at = now
        return table.get(workspace_id)

    return lookup


def notifying(
    indexer: "SessionIndexer | None", sink: Callable[..., Any], session_id: str
) -> Callable[..., Any]:
    """Wrap a commit callback so a successful append also wakes the index.

    The index is never awaited and never raises through this path: a lost notification is caught
    by the next ``reconcile()``, and an index that cannot keep up must not fail a run.
    """
    if indexer is None:
        return sink

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        result = sink(*args, **kwargs)
        indexer.notify(session_id)
        return result

    return wrapped


def _previous_from(row: IndexedSession) -> ScanResult:
    """Carry the session-level facts across an incremental scan (the header is only at offset 0)."""
    return ScanResult(
        session_id=row.session_id,
        created_at=row.created_at,
        workspace_id=row.workspace_id,
        title=row.title,
        first_user_text=row.first_user_text,
        next_seq=None,
        entries=(),
        entry_count=row.entry_count,
        next_offset=row.indexed_bytes,
        truncated_tail=False,
    )


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


__all__ = [
    "DEFAULT_FLUSH_SECONDS",
    "MAX_REPORTED_DETAILS",
    "NOTIFY_SETTLE_SECONDS",
    "WORKSPACE_DIR_PREFIX",
    "SessionIndexer",
    "WORKSPACE_LOOKUP_TTL_SECONDS",
    "notifying",
    "workspace_id_from_path",
    "workspace_lookup",
]
