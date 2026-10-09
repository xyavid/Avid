"""索引层的形状：状态取值、扫描记录、查询结果。

这里只有数据，没有 sqlite、没有文件 IO —— 抽查索引对不对时，这些类型就是判据的载体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# sessions.index_status 的取值（落库字符串，改名等于一次数据迁移）。
INDEX_STATUSES: tuple[str, ...] = (
    "ok",  # 游标追平文件
    "pending",  # 还没跑过
    "stale",  # 文件变了（变短/换 inode/条数不符）→ 需要重建
    "error",  # 上一次失败，last_error 里有原因
    "missing",  # 记录里的文件已经不在了
    "unsupported",  # storageVersion 不认识（不猜，也不崩）
)
INDEX_STATUS_OK, INDEX_STATUS_PENDING, INDEX_STATUS_STALE = "ok", "pending", "stale"
INDEX_STATUS_ERROR, INDEX_STATUS_MISSING, INDEX_STATUS_UNSUPPORTED = "error", "missing", "unsupported"

# What a scan found in one session file; byte ranges are per line because one commit can carry several writes.
@dataclass(frozen=True)
class ScannedEntry:
    entry_id: str
    seq: int
    entry_type: str
    role: str | None
    timestamp: int | None
    byte_offset: int
    byte_length: int
    search_text: str


@dataclass(frozen=True)
class ScanResult:
    """One pass over a session file from a byte offset, plus the session-level facts it carries."""

    session_id: str | None
    created_at: int | None
    workspace_id: str | None
    title: str | None
    first_user_text: str | None
    next_seq: int | None
    entries: tuple[ScannedEntry, ...]
    entry_count: int
    next_offset: int
    truncated_tail: bool
    found_entries: int
    unsupported: str | None = None


@dataclass(frozen=True)
class IndexedSession:
    """One row of ``sessions``; every field is reproducible from JSONL plus the registry."""

    session_id: str
    file_path: str
    workspace_id: str | None = None
    workspace_root: str | None = None
    workspace_name: str | None = None
    title: str | None = None
    first_user_text: str | None = None
    created_at: int | None = None
    updated_at: int | None = None
    file_size: int = 0
    indexed_bytes: int = 0
    entry_count: int = 0
    indexed_at: int | None = None
    index_status: str = INDEX_STATUS_PENDING
    last_error: str | None = None


@dataclass(frozen=True)
class SearchHit:
    """One full-text hit: enough to show a snippet and to jump to the original entry."""

    session_id: str
    entry_id: str
    seq: int
    entry_type: str
    role: str | None
    timestamp: int | None
    byte_offset: int
    byte_length: int
    snippet: str
    title: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None


@dataclass(frozen=True)
class IndexReport:
    """What one indexing pass did; the CLI prints it and the tests assert on it."""

    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    details: tuple[str, ...] = field(default_factory=tuple)

    def merged(self, other: "IndexReport") -> "IndexReport":
        return IndexReport(
            indexed=self.indexed + other.indexed,
            skipped=self.skipped + other.skipped,
            failed=self.failed + other.failed,
            removed=self.removed + other.removed,
            details=self.details + other.details,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "indexed": self.indexed,
            "skipped": self.skipped,
            "failed": self.failed,
            "removed": self.removed,
            "details": list(self.details),
        }
