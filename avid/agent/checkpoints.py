"""Write-ahead snapshots for the two files-tool write paths (write_file / edit_file) and the
/rewind helpers; shell writes stay outside the recoverable promise, since one command can touch
paths that cannot be enumerated before it runs, and nothing prunes old snapshots.

Failure semantics: snapshot returns None on success and an error string when the backup failed,
and the caller must then refuse the write rather than leave a change without a snapshot.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

_CHECKPOINTS_RELPATH = Path(".avid") / "checkpoints"
_MANIFEST = "manifest.json"
_SEQ_WIDTH = 12
_NOTHING_TO_RESTORE = "没有需要恢复的文件"

# Session-layer entry discriminator (session.types.MESSAGE_ENTRY): the kernel never imports the
# session layer, and a unit test built from a real Entry pins this literal.
_TRANSCRIPT_ENTRY = "message"


class DirCheckpointSink:
    """Snapshots into <root>/.avid/checkpoints/<session_id>/<seq>/, built by the session wiring."""

    def __init__(
        self, *, root: Path, session_id: str, tip_seq: Callable[[], int | None]
    ) -> None:
        self._root = Path(root)
        self._session_id = session_id
        self._tip_seq = tip_seq
        self._lock = threading.Lock()
        self._seq: int | None = None
        self._entries: list[dict[str, Any]] = []

    def snapshot(self, path: Path) -> str | None:
        """Snapshot one file before it is written; None is success, a string is the refusal text."""
        with self._lock:
            try:
                return self._snapshot_locked(path)
            except OSError as exc:
                return f"错误：写前快照失败：{exc}；已拒绝写入，文件保持原状"

    def _snapshot_locked(self, path: Path) -> str | None:
        if self._root / ".avid" in path.parents:
            return None  # never snapshot the checkpoints dir itself, or a restore would swallow it
        try:
            seq = self._tip_seq()
        except Exception as exc:  # session closed: no landing point can be attributed, so refuse
            return f"错误：写前快照失败：读取会话落点失败：{exc}；已拒绝写入"
        if seq is None:
            return "错误：写前快照失败：会话还没有可归属的落库条目；已拒绝写入"

        if seq != self._seq:
            self._seq = seq
            self._entries = []
        # The seq dir is derived, not stored: seq is monotonic, so the newest dir is the landing
        # point.
        seq_dir = self._session_dir() / f"{seq:0{_SEQ_WIDTH}d}"
        seq_dir.mkdir(parents=True, exist_ok=True)

        key = str(path)
        if any(entry["path"] == key for entry in self._entries):
            return None  # the earliest pre-write state already won; a later read sees the change

        entry: dict[str, Any] = {"path": key, "blob": None, "absent": True}
        if path.exists():
            blob = f"blob-{len(self._entries):04d}"
            (seq_dir / blob).write_bytes(path.read_bytes())
            entry = {"path": key, "blob": blob, "absent": False}
        self._entries.append(entry)
        self._write_manifest(seq_dir)
        return None

    def _session_dir(self) -> Path:
        return self._root / _CHECKPOINTS_RELPATH / self._session_id

    def _write_manifest(self, seq_dir: Path) -> None:
        staging = seq_dir / (_MANIFEST + ".tmp")
        staging.write_text(
            json.dumps(self._entries, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(staging, seq_dir / _MANIFEST)  # always parseable, so restore needs no repair


def restore(*, root: Path, session_id: str, through_seq: int) -> list[str]:
    """Restore files to their state at session entry seq=through_seq and return report lines, with
    each path taking its earliest snapshot and a corrupted manifest or failed path never blocking
    the rest."""
    session_dir = Path(root) / _CHECKPOINTS_RELPATH / session_id
    if not session_dir.is_dir():
        return [_NOTHING_TO_RESTORE]

    planned: dict[str, tuple[Path, dict[str, Any]]] = {}
    for seq_dir in _seq_dirs(session_dir, after=through_seq):
        try:
            entries = json.loads((seq_dir / _MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # a corrupted manifest is skipped, never blocking the other directories
        for entry in entries:
            planned.setdefault(str(entry.get("path")), (seq_dir, entry))
    if not planned:
        return [_NOTHING_TO_RESTORE]

    lines: list[str] = []
    for key, (seq_dir, entry) in planned.items():
        try:
            if entry.get("absent"):
                Path(key).unlink(missing_ok=True)
                lines.append(f"已删除 {key}（该点时尚不存在）")
                continue
            blob = seq_dir / str(entry.get("blob"))
            target = Path(key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.read_bytes())
            lines.append(f"已恢复 {key}")
        except OSError as exc:
            lines.append(f"恢复失败 {key}：{exc}")
    return lines


def _seq_dirs(session_dir: Path, *, after: int) -> list[Path]:
    """Snapshot directories after seq `after`, ascending; non-numeric names are not snapshots."""
    found = []
    for child in session_dir.iterdir():
        if child.is_dir() and child.name.isdigit() and int(child.name) > after:
            found.append((int(child.name), child))
    return [path for _, path in sorted(found)]


class RewindTarget(NamedTuple):
    """One /rewind landing point: the pointer returns to parent_id and files to through_seq."""

    parent_id: str | None
    through_seq: int


class _ChainEntry(Protocol):
    """Minimal entry shape rewind_target needs, since the kernel never imports the session layer."""

    type: str
    parent_id: str | None
    seq: int
    message: dict[str, Any] | None


def rewind_target(entries: Sequence[_ChainEntry]) -> RewindTarget | None:
    """Find the last user input on the oldest-first branch chain as the /rewind anchor, where only a
    message entry with role=user qualifies (a notice is kernel-injected) and the anchor's seq is
    also restore's through_seq."""
    for entry in reversed(entries):
        if entry.type != _TRANSCRIPT_ENTRY or entry.message is None:
            continue
        if entry.message.get("role") != "user":
            continue
        return RewindTarget(entry.parent_id, entry.seq)
    return None


def restore_tally(lines: Sequence[str]) -> tuple[int, int]:
    """Fold restore's report lines into (restored, deleted); a failed line counts as neither."""
    return (
        sum(1 for line in lines if line.startswith("已恢复")),
        sum(1 for line in lines if line.startswith("已删除")),
    )
