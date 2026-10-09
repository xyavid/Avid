"""Turn session files into records (bytes to lines to records): a byte range is the range of
the whole line, because one commit writes one line and several writes in it share that line.

Only complete lines count, so a torn tail stays for the next pass (the same rule as
`_split_complete_lines`), and an incremental scan takes the session facts from the caller because
the header sits at offset 0.
"""

from __future__ import annotations

from pathlib import Path

from ..session.errors import SessionStorageError
from ..session.jsonl import parse_header, parse_transaction
from ..session.types import (
    STORAGE_VERSION,
    CommittedEntry,
    CommittedValueSet,
)
from ..session.values import COMPACTION_NS, SESSION_NAME_NS
from .extract import compaction_text, entry_text, one_line
from .types import ScannedEntry, ScanResult

# A compaction summary is its own entry type in the index, not a message or a plain value.
COMPACTION_TYPE = "compaction"


def read_session_facts(path: Path) -> tuple[str, int, str | None, str | None]:
    """Read just the header line: (session_id, created_at, workspace_id, unsupported_reason)."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = handle.readline()
    except OSError as exc:
        raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc
    if not first.endswith("\n"):
        raise SessionStorageError(f"会话文件缺少完整 header：{path}")
    header = parse_header(first.rstrip("\n"))
    if header.storage_version != STORAGE_VERSION:
        return header.id, header.created_at, header.workspace, f"storageVersion={header.storage_version}"
    return header.id, header.created_at, header.workspace, None


def scan_file(
    path: Path, *, start_offset: int = 0, previous: ScanResult | None = None
) -> ScanResult:
    """Scan from ``start_offset`` to the last complete line and report what it found.

    ``entries`` holds only the rows this pass produced; ``entry_count`` is the file's running
    total (previous count plus this pass), because that is the number a list page shows.
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc

    session_id = previous.session_id if previous else None
    created_at = previous.created_at if previous else None
    workspace_id = previous.workspace_id if previous else None
    title = previous.title if previous else None
    first_user = previous.first_user_text if previous else None
    next_seq = previous.next_seq if previous else None
    entry_count = previous.entry_count if previous else 0

    if start_offset > size:
        # File shorter than the cursor: truncated or replaced; let the caller rebuild, do not guess.
        raise SessionStorageError(f"游标越界：游标 {start_offset} > 文件长度 {size}")

    try:
        with path.open("rb") as handle:
            handle.seek(start_offset)
            tail = handle.read()
    except OSError as exc:
        raise SessionStorageError(f"会话文件读不了：{path}（{exc}）") from exc

    truncated_tail = not tail.endswith(b"\n")
    if truncated_tail:
        cut = tail.rfind(b"\n")
        tail = b"" if cut == -1 else tail[: cut + 1]

    entries: list[ScannedEntry] = []
    offset = start_offset
    for raw in tail.split(b"\n")[:-1]:
        line_length = len(raw) + 1  # +1 for the newline: ranges count against the original file
        line_offset = offset
        offset += line_length
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SessionStorageError(f"会话文件不是 UTF-8：{path}（偏移 {line_offset}）") from exc

        if line_offset == 0:
            header = parse_header(text)
            session_id = header.id
            created_at = header.created_at
            workspace_id = header.workspace
            next_seq = header.next_seq
            if header.storage_version != STORAGE_VERSION:
                return ScanResult(
                    session_id=session_id,
                    created_at=created_at,
                    workspace_id=workspace_id,
                    title=title,
                    first_user_text=first_user,
                    next_seq=next_seq,
                    entries=(),
                    entry_count=entry_count,
                    next_offset=0,
                    truncated_tail=truncated_tail,
                    unsupported=f"storageVersion={header.storage_version}",
                )
            continue

        for record in parse_transaction(text):
            if isinstance(record, CommittedEntry):
                role, search_text = entry_text(record.entry.type, record.entry.message)
                if role == "user" and first_user is None:
                    content = record.entry.message.get("content") if record.entry.message else None
                    first_user = one_line(content)
                entries.append(
                    ScannedEntry(
                        entry_id=record.entry.id,
                        seq=record.seq,
                        entry_type=record.entry.type,
                        role=role,
                        timestamp=record.timestamp,
                        byte_offset=line_offset,
                        byte_length=line_length,
                        search_text=search_text,
                    )
                )
                entry_count += 1
            elif isinstance(record, CommittedValueSet) and record.namespace == SESSION_NAME_NS:
                title = one_line(record.value) or title
            elif isinstance(record, CommittedValueSet) and record.namespace == COMPACTION_NS:
                # Summary lives only in a cursor value; a deterministic id keeps it searchable.
                summary_text = compaction_text(record.value)
                if summary_text:
                    entries.append(
                        ScannedEntry(
                            entry_id=compaction_entry_id(record.key, record.seq),
                            seq=record.seq,
                            entry_type=COMPACTION_TYPE,
                            role=None,
                            timestamp=None,
                            byte_offset=line_offset,
                            byte_length=line_length,
                            search_text=summary_text,
                        )
                    )
                    entry_count += 1

    return ScanResult(
        session_id=session_id,
        created_at=created_at,
        workspace_id=workspace_id,
        title=title,
        first_user_text=first_user,
        next_seq=next_seq,
        entries=tuple(entries),
        entry_count=entry_count,
        next_offset=offset,
        truncated_tail=truncated_tail,
    )


def compaction_entry_id(key: str, seq: int) -> str:
    """Deterministic id for a value row: rescanning the same line yields the same id."""
    return f"value:{COMPACTION_NS}:{key}:{seq}"


__all__ = ["COMPACTION_TYPE", "compaction_entry_id", "read_session_facts", "scan_file"]
