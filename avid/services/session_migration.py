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
from ..session.errors import SessionError, SessionLockedError, SessionStorageError
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
        # 两种形状都扫：集中目录的子目录（id 取目录名）与直接躺在这一层的平铺会话。
        # 只挑一种会让混合目录里的另一批静默漏掉（用户只会看到「没有可搬的会话」）。
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
    if not _safe_component(owner):
        # 归属来自文件内容，而文件可能来自别人的仓库：它不能当路径分量使。
        return None, Skip(file, f"header 里的 workspaceId 不能当目录名：{owner!r}")

    if file.is_symlink():
        # 搬链接只会把链接搬过去（真身留在原处），而列表的符号链接守卫会把这种条目藏起来
        # ——用户会以为搬成功了却永远看不到它。让真身自己来。
        return None, Skip(file, "是符号链接：先自己决定用真身还是链接，这里不搬")

    target = store / owner / file.name
    if target.resolve() == file.resolve():
        return None, None  # 已经在新位置（--from 指到集中目录自身）
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
    """(在用吗, 别的原因)。

    计划阶段**不写盘**：锁文件不存在就说明从来没有进程打开过这个会话（写者一打开就会建它），
    不必为了探一下而把 `.lock` 造出来。锁文件在、但拿不到 → 真有人在用；开不了锁文件本身
    （权限/磁盘）→ 如实报原因，别谎报成「正在被使用」。
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
