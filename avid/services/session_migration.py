"""一次性迁移：把旧布局里的会话搬进集中会话目录（阶段 56）。

旧布局 = 每个工作区根下的 ``.avid/sessions``；新布局 = ``<会话目录>/<工作区 id>/``。
归属只认文件 header 里的 workspaceId（没有就按来源目录推），目标同名文件一律不覆盖，
正被别的进程持有的会话也不搬——迁移不是抢占。搬完只删空的旧目录（``.avid`` 下还有
checkpoints 与 context 溢写，留着）。

借用会话层的三个内部件是有意的：header 编解码是磁盘格式的唯一真相、旁挂锁是
「有没有人在用」的唯一判据、文件后缀决定什么算会话文件。它们不能在这儿重写一份。
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
from ..session.errors import SessionError, SessionStorageError
from ..session.jsonl import SUFFIX, SessionFileLock, read_header
from .workspace_registry import derive_id

# 旧布局里会话目录相对于工作区根的位置。
LEGACY_RELPATH = Path(".avid") / "sessions"
# 集中目录下按工作区 id 命名的子目录前缀（derive_id 的产物）。
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
    """搬迁清单：只描述，不动盘（执行是 apply_migration）。"""

    store: Path
    moves: tuple[Move, ...]
    skips: tuple[Skip, ...]

    @property
    def source_dirs(self) -> tuple[Path, ...]:
        """搬空后可能可以删掉的旧目录（按首次出现的顺序去重）。"""
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
    """扫描旧目录并列出搬迁清单；不写盘。

    ``roots`` 是工作区根（取各自的 ``.avid/sessions``，归属兜底按根路径摘要算）；
    ``from_dir`` 额外给一个目录：下面有 ``w-*`` 子目录时当成集中目录逐个扫（id 取子目录名），
    否则当成一个平铺目录（归属只认 header）。
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
        subdirs = [
            child
            for child in sorted(given.glob(f"{WORKSPACE_DIR_PREFIX}*"))
            if child.is_dir()
        ]
        if subdirs:
            candidates += [(child, child.name) for child in subdirs]
        else:
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
    """按清单搬：同设备 rename，跨设备复制校验后再删源；只删搬空的旧目录。

    计划与执行之间可能有别人插进来（建了同名会话、或打开了同一个会话），所以每一步
    都复查一次而不是相信计划——复查只缩小窗口，不消除竞态：这是本地单用户工具，
    真要有并发写，旁挂锁会在更早的地方拦住（同一个会话同一时刻只允许一个写入者）。
    """
    moved: list[Move] = []
    skipped: list[Skip] = list(plan.skips)
    for move in plan.moves:
        if move.target.exists():
            skipped.append(Skip(move.source, f"目标已存在：{move.target}"))
            continue
        if _in_use(move.source):
            skipped.append(Skip(move.source, "正在被另一个进程使用"))
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
        except OSError:  # 还有别的东西，或已经不在了：留着
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

    target = store / owner / file.name
    if target.resolve() == file.resolve():
        return None, None  # 已经在新位置（--from 指到集中目录自身）
    if target.exists():
        return None, Skip(file, f"目标已存在：{target}")
    if _in_use(file):
        return None, Skip(file, "正在被另一个进程使用")
    return Move(source=file, target=target, workspace_id=owner), None


def _in_use(path: Path) -> bool:
    """拿一下旁挂锁：拿得到说明没人在用（随即放开）；拿不到、或锁都开不了，都算在用。"""
    lock = SessionFileLock(path)
    try:
        lock.acquire(f"迁移 {path.name}")
    except SessionError:
        return True
    lock.release()
    return False


def _drop_lock(source: Path) -> None:
    """顺手清掉旁挂锁文件；拿不到就留着（真有别人在用，删了等于把互斥拆了）。"""
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
    """同设备一步改名；跨设备先写到临时名、fsync、改名，最后才删源。"""
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
