"""Materialised session state: the entries, values and stats that both backends share in memory."""

from __future__ import annotations

import copy
import threading
from collections.abc import Sequence
from dataclasses import replace

from .errors import SessionInvariantError
from .types import (
    MESSAGE_ENTRY,
    BranchScan,
    CommittedEntry,
    CommittedValueDelete,
    CommittedValueSet,
    CommittedWrite,
    Entry,
    EntryQuery,
    EntryWrite,
    PreparedCommit,
    SessionStats,
    StoredValue,
    ValueDeleteWrite,
    ValueSetWrite,
    Write,
)
from .values import ValueAddress


def _copy_entry(entry: Entry) -> Entry:
    """The read path hands out a deep copy, so callers cannot mutate the state held here."""
    return replace(entry, message=copy.deepcopy(entry.message))


def _copy_value(stored: StoredValue) -> StoredValue:
    return replace(stored, value=copy.deepcopy(stored.value))


class SessionState:
    """Entries, values and stats in memory, read and written under one reentrant lock."""

    def __init__(self, next_seq: int = 1) -> None:
        self._entries: dict[str, Entry] = {}
        self._by_seq: list[Entry] = []
        self._values: dict[tuple[str, str], StoredValue] = {}
        self._message_count = 0
        self._next_seq = next_seq
        # Reentrant because read methods call each other (stats and scans read the same maps).
        self._lock = threading.RLock()

    # Write path: prepare, validate, apply.

    def prepare_commit(self, writes: Sequence[Write], timestamp: int) -> PreparedCommit:
        """Assigns consecutive seq values and one timestamp, then validates; state is untouched."""
        with self._lock:
            return self._prepare_commit(writes, timestamp)

    def _prepare_commit(
        self, writes: Sequence[Write], timestamp: int
    ) -> PreparedCommit:
        committed: list[CommittedWrite] = []
        for offset, write in enumerate(writes):
            seq = self._next_seq + offset
            if isinstance(write, EntryWrite):
                committed.append(CommittedEntry(seq, timestamp, write.entry))
            elif isinstance(write, ValueSetWrite):
                committed.append(CommittedValueSet(seq, write.namespace, write.key, write.value))
            elif isinstance(write, ValueDeleteWrite):
                committed.append(CommittedValueDelete(seq, write.namespace, write.key))
            else:  # pragma: no cover - only a developer can construct another write type
                raise SessionInvariantError(f"未知的写入类型：{type(write).__name__}")
        self.validate(committed)
        return PreparedCommit(
            writes=tuple(committed),
            first_seq=self._next_seq,
            seqs=tuple(item.seq for item in committed),
            timestamp=timestamp,
        )

    def validate(self, writes: Sequence[CommittedWrite]) -> None:
        """Validation used before writing and on replay: monotonic seq, unique ids, live parents."""
        with self._lock:
            self._validate(writes)

    def _validate(self, writes: Sequence[CommittedWrite]) -> None:
        # Every write must land strictly after the highest seq already applied.
        previous_seq = self._next_seq - 1
        seen_ids: set[str] = set()
        for write in writes:
            if write.seq <= previous_seq:
                raise SessionInvariantError(
                    f"存储 seq 非单调：{write.seq} 不大于 {previous_seq}"
                )
            previous_seq = write.seq
            if not isinstance(write, CommittedEntry):
                continue
            entry = write.entry
            if entry.id in self._entries or entry.id in seen_ids:
                raise SessionInvariantError(f"条目 id 重复：{entry.id}")
            if (
                entry.parent_id is not None
                and entry.parent_id not in self._entries
                and entry.parent_id not in seen_ids
            ):
                raise SessionInvariantError(
                    f"条目的 parent 不存在：{entry.parent_id}"
                )
            seen_ids.add(entry.id)

    def apply(self, writes: Sequence[CommittedWrite]) -> SessionStats:
        """Lands writes that already passed validation and returns the resulting stats."""
        with self._lock:
            return self._apply(writes)

    def _apply(self, writes: Sequence[CommittedWrite]) -> SessionStats:
        for write in writes:
            if isinstance(write, CommittedEntry):
                entry = Entry(
                    id=write.entry.id,
                    parent_id=write.entry.parent_id,
                    seq=write.seq,
                    timestamp=write.timestamp,
                    type=write.entry.type,
                    message=write.entry.message,
                )
                # Store a copy: a writer changing its dict after commit must not change state.
                entry = replace(entry, message=copy.deepcopy(entry.message))
                self._entries[entry.id] = entry
                self._by_seq.append(entry)
                if entry.type == MESSAGE_ENTRY:
                    self._message_count += 1
            elif isinstance(write, CommittedValueSet):
                self._values[(write.namespace, write.key)] = StoredValue(
                    namespace=write.namespace,
                    key=write.key,
                    value=write.value,
                    seq=write.seq,
                )
            else:
                self._values.pop((write.namespace, write.key), None)
            self._next_seq = write.seq + 1
        return self.stats

    def advance_next_seq(self, next_seq: int) -> None:
        """Adopts the header high-water mark, but only when it is ahead of the replayed seq."""
        if not isinstance(next_seq, int) or next_seq < 1:
            raise SessionInvariantError(f"非法的 seq 高水位：{next_seq!r}")
        with self._lock:
            self._next_seq = max(self._next_seq, next_seq)

    # Read path: every accessor takes the same lock and copies out.

    @property
    def next_seq(self) -> int:
        with self._lock:
            return self._next_seq

    @property
    def stats(self) -> SessionStats:
        with self._lock:
            return SessionStats(message_count=self._message_count)

    def get_entries(self, ids: Sequence[str]) -> dict[str, Entry]:
        with self._lock:
            found: dict[str, Entry] = {}
            for entry_id in ids:
                entry = self._entries.get(entry_id)
                if entry is not None:
                    found[entry_id] = _copy_entry(entry)
            return found

    def get_value(self, address: ValueAddress) -> StoredValue | None:
        with self._lock:
            stored = self._values.get((address.namespace, address.key))
            return None if stored is None else _copy_value(stored)

    def values_in(self, namespace: str) -> list[StoredValue]:
        """All values in a namespace, ordered by seq; branch names come from enumerating one."""
        with self._lock:
            found = [
                _copy_value(item)
                for item in self._values.values()
                if item.namespace == namespace
            ]
            found.sort(key=lambda item: item.seq)
            return found

    def scan_branch(self, query: BranchScan) -> list[Entry]:
        """Walks back from start along parent_id and returns that chain."""
        with self._lock:
            return self._scan_branch(query)

    @staticmethod
    def _branch_match(entry: Entry, query: BranchScan) -> bool:
        if query.type is not None and entry.type != query.type:
            return False
        if query.cursor_seq is None:
            return True
        # The cursor is exclusive: ascending takes later seqs, descending earlier ones.
        if query.order == "oldestFirst":
            return entry.seq > query.cursor_seq
        return entry.seq < query.cursor_seq

    def _scan_branch(self, query: BranchScan) -> list[Entry]:
        # There is no session-level default here: a branch scan needs a concrete start entry.
        if query.start is None:
            raise SessionInvariantError("scan_branch 需要一个起点条目 id")
        start = self._entries.get(query.start)
        if start is None:
            raise SessionInvariantError(f"分支起点不存在：{query.start}")

        limit = None if query.limit is None else max(0, query.limit)
        newest_first = query.order != "oldestFirst"

        path: list[Entry] = []
        cursor: Entry | None = start
        while cursor is not None:
            if self._branch_match(cursor, query):
                path.append(cursor)
                # newestFirst can stop once the page is full, which matters for long chains.
                if newest_first and limit is not None and len(path) >= limit:
                    break
            if cursor.parent_id is None:
                break
            parent = self._entries.get(cursor.parent_id)
            if parent is None:
                raise SessionInvariantError(
                    f"链断了：{cursor.id} 的 parent {cursor.parent_id} 不存在"
                )
            cursor = parent

        if newest_first:
            filtered = path
        else:
            # oldestFirst must reach the root to know the oldest end, so the limit comes later.
            path.reverse()
            filtered = [item for item in path if self._branch_match(item, query)]
        page = filtered if limit is None else filtered[:limit]
        return [_copy_entry(item) for item in page]

    def scan_entries(self, query: EntryQuery) -> list[Entry]:
        """Global scan by seq, newest first by default."""
        with self._lock:
            return self._scan_entries(query)

    def _scan_entries(self, query: EntryQuery) -> list[Entry]:
        limit = float("inf") if query.limit is None else max(0, query.limit)
        descending = query.order == "desc"
        found: list[Entry] = []
        items = reversed(self._by_seq) if descending else iter(self._by_seq)
        for entry in items:
            if query.type is not None and entry.type != query.type:
                continue
            if query.cursor_seq is not None:
                # The cursor is exclusive: the next page starts strictly after the previous one.
                if descending and entry.seq >= query.cursor_seq:
                    continue
                if not descending and entry.seq <= query.cursor_seq:
                    continue
            found.append(entry)
            if len(found) >= limit:
                break
        return [_copy_entry(item) for item in found]
