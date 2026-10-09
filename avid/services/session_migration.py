"""One-shot migration of sessions from the legacy ``<workspace root>/.avid/sessions`` layout into
the shared ``<session dir>/<workspace id>/`` store. Ownership comes from the header's workspaceId
(falling back to the source directory), an existing target is never overwritten and a session
held by another process is never moved, so migration never preempts; the session package's header
codec, sidecar lock and file suffix are reused as the only truth of what a session file is.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from ..security import userdirs
from ..session.errors import SessionError, SessionLockedError, SessionStorageError
from ..session.jsonl import SUFFIX, SessionFileLock, read_header
from .workspace_registry import derive_id

# Legacy session directory relative to the workspace root.
LEGACY_RELPATH = Path(".avid") / "sessions"
# Prefix of the per-workspace subdirectories under the shared store (derive_id's output).
WORKSPACE_DIR_PREFIX = "w-"


@dataclass(frozen=True)
class Move:
    source: Path
    target: Path
    workspace_id: str


@dataclass(frozen=True)
class Skip:
    source: Path
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    """A migration plan: description only, nothing touched on disk (executed by apply_migration)."""

    store: Path
    moves: tuple[Move, ...]
    skips: tuple[Skip, ...]

    @property
    def source_dirs(self) -> tuple[Path, ...]:
        """Legacy directories that may become removable once emptied, deduplicated in order."""
        seen: dict[Path, None] = {}
        for move in self.moves:
            seen.setdefault(move.source.parent, None)
        return tuple(seen)


@dataclass(frozen=True)
class MigrationTally:
    moved: tuple[Move, ...]
    skipped: tuple[Skip, ...]
    removed_dirs: tuple[Path, ...]


def plan_migration(
    *,
    store: str | Path | None = None,
    roots: Iterable[str | Path] = (),
    from_dir: str | Path | None = None,
) -> MigrationPlan:
    """Scan the legacy locations and list the moves without writing to disk: each root contributes
    its ``.avid/sessions`` (ownership falls back to the path digest), while ``from_dir`` is scanned
    as a shared store when it holds ``w-*`` subdirectories and as a flat directory otherwise.
    """
    target_store = (
        Path(store).expanduser() if store is not None else userdirs.sessions_dir()
    )

    candidates: list[tuple[Path, str | None]] = []
    for root in roots:
        path = Path(root).expanduser()
        candidates.append((path / LEGACY_RELPATH, derive_id(path)))
    if from_dir is not None:
        given = Path(from_dir).expanduser()
        # Both shapes are scanned, shared-store subdirectories and flat files in this same
        # directory; picking one would silently miss the other half of a mixed directory.
        subdirs = [
            child
            for child in sorted(given.glob(f"{WORKSPACE_DIR_PREFIX}*"))
            if child.is_dir()
        ]
        candidates += [(child, child.name) for child in subdirs]
        candidates.append((given, None))

    moves: list[Move] = []
    skips: list[Skip] = []
    seen: set[Path] = set()
    for directory, fallback in candidates:
        if directory.resolve() in seen:
            continue
        seen.add(directory.resolve())
        if not directory.is_dir():
            continue
        for file in sorted(directory.glob(f"*{SUFFIX}")):
            move, skip = _plan_one(file, fallback=fallback, store=target_store)
            if move is not None:
                moves.append(move)
            elif skip is not None:
                skips.append(skip)
    return MigrationPlan(store=target_store, moves=tuple(moves), skips=tuple(skips))


def apply_migration(plan: MigrationPlan) -> MigrationTally:
    """Execute the plan: rename within one device, copy-verify-then-delete across devices, and
    remove only the emptied legacy directories; every step is re-checked rather than trusted,
    which narrows the race window without closing it, while the sidecar lock keeps a session
    single-writer.
    """
    moved: list[Move] = []
    skipped: list[Skip] = list(plan.skips)
    for move in plan.moves:
        if move.target.exists():
            skipped.append(Skip(move.source, f"目标已存在：{move.target}"))
            continue
        in_use, reason = _in_use(move.source)
        if in_use:
            skipped.append(
                Skip(move.source, f"正在被另一个进程使用（{reason}）" if reason else "正在被另一个进程使用")
            )
            continue
        try:
            _move_file(move.source, move.target)
        except OSError as exc:
            skipped.append(Skip(move.source, f"搬不动：{exc}"))
            continue
        _drop_lock(move.source)
        moved.append(move)

    removed: list[Path] = []
    for directory in plan.source_dirs:
        try:
            directory.rmdir()
        except OSError:  # something else is still there, or it is gone: keep it
            continue
        removed.append(directory)
    return MigrationTally(
        moved=tuple(moved), skipped=tuple(skipped), removed_dirs=tuple(removed)
    )


def _plan_one(file: Path, *, fallback: str | None, store: Path) -> tuple[Move | None, Skip | None]:
    try:
        header = read_header(file)
    except (OSError, SessionStorageError) as exc:
        return None, Skip(file, f"这不是本程序的会话文件（{exc}）")

    owner = header.workspace or fallback
    if not owner:
        return None, Skip(file, "header 里没有 workspaceId，认不出归属")
    if not _safe_component(owner):
        # Ownership comes from file content that may come from someone else's repository, so it
        # cannot be used as a path component.
        return None, Skip(file, f"header 里的 workspaceId 不能当目录名：{owner!r}")

    if file.is_symlink():
        # Moving a symlink moves only the link while the listing's symlink guard hides such an
        # entry, so the user would see a successful move that never appears; the real file must
        # come on its own.
        return None, Skip(file, "是符号链接：先自己决定用真身还是链接，这里不搬")

    target = store / owner / file.name
    if target.resolve() == file.resolve():
        return None, None  # already in place (--from pointed at the shared store itself)
    if target.exists():
        return None, Skip(file, f"目标已存在：{target}")
    in_use, reason = _in_use(file)
    if in_use:
        return None, Skip(file, f"正在被另一个进程使用（{reason}）" if reason else "正在被另一个进程使用")
    return Move(source=file, target=target, workspace_id=owner), None


def _safe_component(value: str) -> bool:
    """A workspace id must be a single plain path component: no separators, no traversal, no NUL."""
    if value in ("", ".", "..") or "\x00" in value:
        return False
    return "/" not in value and "\\" not in value


def _in_use(path: Path) -> tuple[bool, str | None]:
    """(in use, other reason); planning never writes, so a missing lock file means no process
    ever opened the session, and a lock that cannot be taken reports its true reason instead of
    claiming the session is in use.
    """
    lock = SessionFileLock(path)
    if not lock.path.exists():
        return False, None
    try:
        lock.acquire(f"迁移 {path.name}")
    except SessionLockedError:
        return True, None
    except SessionError as exc:
        return True, str(exc)
    lock.release()
    return False, None


def _drop_lock(source: Path) -> None:
    """Drop the sidecar lock file when it can be taken; otherwise leave it, since deleting a
    held lock would tear down the mutual exclusion.
    """
    lock = SessionFileLock(source)
    if not lock.path.exists():
        return
    try:
        lock.acquire(f"清理 {source.name}")
    except SessionError:
        return
    lock.release()
    with contextlib.suppress(OSError):
        lock.path.unlink()


def _move_file(source: Path, target: Path) -> None:
    """Rename in one step on one device; across devices write a temp file, fsync, rename, then
    delete the source last.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    temp = target.with_name(target.name + ".migrating")
    with contextlib.suppress(OSError):
        temp.unlink()
    shutil.copy2(source, temp)
    with temp.open("rb") as handle:
        os.fsync(handle.fileno())
    if temp.stat().st_size != source.stat().st_size:
        raise OSError(f"复制不完整，源文件没动：{temp}")
    os.replace(temp, target)
    source.unlink()
