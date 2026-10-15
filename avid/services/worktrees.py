"""The root one run works on: the workspace itself, or a git worktree of its own when the workspace
root is already taken by another session.

The decision is made at a session's first run and recorded in that session (`avid.worktree`), so a
later run lands on the same path; the workspace root, meanwhile, belongs to one session at a time.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..security import userdirs
from ..session.values import session_worktree

logger = logging.getLogger("avid.services.worktrees")

BRANCH_PREFIX = "avid/session-"
# Seconds: a worktree add on a large repository is the slow case, and a hung git must not hold a run.
GIT_TIMEOUT_SECONDS = 60
# Enough to show what a session did without paging; the panel says so when there are more.
COMMIT_LIMIT = 20
#: Directory under the worktrees root holding one claim file per workspace, named by workspace id.
ROOTS_DIRNAME = "roots"
# Session ids allow almost anything (only /, \\ and NUL are refused), so the directory and branch
# name both need reducing; over-long ids keep a digest so two ids never fold into one name.
MAX_SLUG_CHARS = 48
_SLUG_DROP = re.compile(r"[^A-Za-z0-9._-]+")
_DOT_RUN = re.compile(r"\.{2,}")

WORKTREE_KEY = "worktree"


class WorktreeError(Exception):
    """The session's work root cannot be used; the message is written for the user to read directly."""


@dataclass(frozen=True)
class Worktree:
    """Where one session works: its root and, when that root is a worktree, its branch and base.

    ``reason`` says why a session works on the workspace root instead of a tree of its own; it is
    None both for the session that owns that root and for one that has its own tree.
    """

    path: str
    branch: str | None = None
    base: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "branch": self.branch, "base": self.base, "reason": self.reason}


@dataclass
class _Probe:
    """What git says about a candidate root; ``blocker`` is the reason no tree can be made."""

    blocker: str | None = None
    head: str | None = None
    notes: list[str] = field(default_factory=list)


def _git(path: str | Path, *args: str, check: bool = True) -> str | None:
    """Run one git command in path; check=False returns None instead of raising on a refusal."""
    try:
        done = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if not check:
            return None
        raise WorktreeError(f"git 起不来：{exc}") from exc
    if done.returncode != 0:
        if not check:
            return None
        message = (done.stderr or done.stdout).strip().splitlines()
        raise WorktreeError(f"git {' '.join(args)} 失败：{message[-1] if message else done.returncode}")
    # Trailing newlines only: `status --porcelain` marks a change in column 1 with a leading blank,
    # so stripping the whole output would shift every line left by one.
    return done.stdout.rstrip("\r\n")


def _probe(root: Path) -> _Probe:
    """Whether a tree can be made for this workspace, and the commit it would start from."""
    if shutil.which("git") is None:
        return _Probe(blocker="这台机器上没有 git")
    top = _git(root, "rev-parse", "--show-toplevel", check=False)
    if not top:
        return _Probe(blocker="工作区不是 git 仓库")
    if Path(top).resolve() != root.resolve():
        # A worktree is repo-wide: one for a workspace below the top level would reach outside the
        # workspace the sandbox is rooted at.
        return _Probe(blocker=f"工作区不是仓库顶层（仓库根在 {top}）")
    head = _git(root, "rev-parse", "HEAD", check=False)
    if not head:
        return _Probe(blocker="仓库还没有提交")
    return _Probe(head=head)


def _slug(session_id: str) -> str:
    """The session id as one directory and branch component; empty or over-long ids get a digest."""
    cleaned = _DOT_RUN.sub(".", _SLUG_DROP.sub("-", session_id)).strip("-. ")
    if not cleaned:
        cleaned = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:12]
    if len(cleaned) > MAX_SLUG_CHARS:
        digest = hashlib.sha1(session_id.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[: MAX_SLUG_CHARS - 9]}-{digest}"
    if cleaned.endswith(".lock"):
        cleaned = f"{cleaned[:-5]}.tree"
    return cleaned


def _claim_path(workspace_id: str) -> Path:
    return userdirs.worktrees_dir() / ROOTS_DIRNAME / workspace_id


def _claim_root(workspace_id: str, session_id: str) -> str | None:
    """Take the workspace root for this session; returns the owner when another session has it.

    Exclusive create is the lock: two runs starting at once cannot both find the root free, and a
    claim read back always names the session that wrote it (one line, so it can be inspected).
    """
    path = _claim_path(workspace_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            owner = path.read_text(encoding="utf-8").strip()
        except OSError:  # pragma: no cover - the file vanished between create and read
            return None
        return None if owner == session_id else owner
    except OSError as exc:
        raise WorktreeError(f"占用工作区根失败：{exc}") from exc
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(session_id)
    return None


def _drop_claim(workspace_id: str, session_id: str) -> None:
    """Give up the root, but only when this session is the one holding it."""
    path = _claim_path(workspace_id)
    try:
        if path.read_text(encoding="utf-8").strip() == session_id:
            path.unlink(missing_ok=True)
    except OSError:  # pragma: no cover - a missing claim is the state we want anyway
        return


def recorded(session: Any) -> Worktree | None:
    """The root this session was assigned, or None before its first run."""
    stored = session.get_value(session_worktree())
    if stored is None:
        return None
    value = stored.value
    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
        logger.warning("工作根记录形状不对，按没记录处理：%r", value)
        return None
    branch, base, reason = value.get("branch"), value.get("base"), value.get("reason")
    return Worktree(
        path=value["path"],
        branch=branch if isinstance(branch, str) else None,
        base=base if isinstance(base, str) else None,
        reason=reason if isinstance(reason, str) else None,
    )


def resolve(session: Any, workspace: Any, *, scratch: bool = False) -> Worktree:
    """This session's work root, decided once; a recorded tree is reused or rebuilt where it stood."""
    known = recorded(session)
    if known is None:
        known = _decide(session.metadata.id, workspace, scratch=scratch)
        session.set_value(session_worktree(), known.to_dict())
        logger.info(
            "会话 %s 的工作根：%s%s",
            session.metadata.id,
            known.path,
            f"（分支 {known.branch}）" if known.branch else f"（{known.reason or '占用工作区根'}）",
        )
        return known
    return known if known.branch is None else _ensure(workspace, known)


def _decide(session_id: str, workspace: Any, *, scratch: bool) -> Worktree:
    """The first decision for a session: its own tree, or the workspace root with a reason."""
    root = Path(workspace.root)
    if scratch:
        # A scratch run is read-only (tools and sandbox alike), and a tree is only worth its cost
        # when two runs can write at once.
        return Worktree(str(root), reason="白板会话只读，不需要自己的工作树")
    probe = _probe(root)
    if probe.blocker is not None:
        _claim_root(workspace.id, session_id)
        return Worktree(str(root), reason=probe.blocker)
    if _claim_root(workspace.id, session_id) is None:
        return Worktree(str(root))
    slug = _slug(session_id)
    path = (userdirs.worktrees_dir() / workspace.id / slug).resolve()
    branch = f"{BRANCH_PREFIX}{slug}"
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(workspace.root, "worktree", "add", "-b", branch, str(path), str(probe.head))
    return Worktree(str(path), branch=branch, base=probe.head)


def _ensure(workspace: Any, known: Worktree) -> Worktree:
    """Keep the recorded tree where it was; a tree removed outside Avid is put back, not replaced."""
    path = Path(known.path)
    if path.is_dir():
        if _git(path, "rev-parse", "--show-toplevel", check=False):
            return known
        raise WorktreeError(
            f"会话的工作树位置被别的东西占着：{known.path}；"
            f"把它挪开或删掉这个会话（它的分支 {known.branch} 还在）"
        )
    if not _git(workspace.root, "rev-parse", "--verify", f"refs/heads/{known.branch}", check=False):
        raise WorktreeError(
            f"会话的工作树不见了：{known.path}（分支 {known.branch} 也没了）；"
            "把它检回来，或删掉这个会话重开一个"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(workspace.root, "worktree", "add", str(path), str(known.branch))
    return known


def release(session: Any, workspace: Any) -> str | None:
    """Give up this session's claim and checkout; returns a note when something was kept back.

    The branch is the session's work and outlives the session; only a checkout with nothing in it
    goes, and a tree holding uncommitted changes is left alone rather than emptied.
    """
    known = recorded(session)
    if known is None or known.branch is None:
        _drop_claim(workspace.id, session.metadata.id)
        return None
    path = Path(known.path)
    if path.is_dir() and (_git(path, "status", "--porcelain", check=False) or "").strip():
        return f"工作树还有未提交的改动，留在 {known.path}（分支 {known.branch}）"
    if path.is_dir():
        _git(workspace.root, "worktree", "remove", str(path))
    if _ahead(known, workspace.root) == 0:
        # Nothing on the branch: it would only be a stray name in the branch list.
        _git(workspace.root, "branch", "-D", str(known.branch), check=False)
    _drop_claim(workspace.id, session.metadata.id)
    return None


def _ahead(known: Worktree, path: str | Path) -> int | None:
    """Commits on the branch since the base it was created from; None when git cannot say.

    Asked of the branch, not of the checkout, so it still answers while the tree is being removed.
    """
    if known.base is None or known.branch is None:
        return None
    count = _git(path, "rev-list", "--count", f"{known.base}..{known.branch}", check=False)
    try:
        return int(count) if count else None
    except ValueError:  # pragma: no cover - git prints a number or nothing
        return None


def facts(known: Worktree) -> dict[str, Any]:
    """What the session's panel shows: where it works, what changed there, what it committed."""
    reading: dict[str, Any] = {
        "worktree": known.branch is not None,
        "path": known.path,
        "branch": known.branch,
        "base": known.base,
        "reason": known.reason,
        "dirty": [],
        "commits": [],
        "ahead": None,
        "note": None,
    }
    if known.branch is None:
        return reading
    path = Path(known.path)
    if not path.is_dir():
        reading["note"] = "工作树不在了"
        return reading
    status = _git(path, "status", "--porcelain", check=False)
    if status is None:
        reading["note"] = "读不到这棵树的改动"
        return reading
    reading["dirty"] = [
        {"status": line[:2], "path": line[3:]} for line in status.splitlines() if line.strip()
    ]
    reading["ahead"] = _ahead(known, path)
    log = _git(
        path,
        "log",
        f"-n{COMMIT_LIMIT}",
        "--format=%h\x1f%s\x1f%ct",
        f"{known.base}..HEAD",
        check=False,
    )
    for line in (log or "").splitlines():
        sha, _, rest = line.partition("\x1f")
        subject, _, committed = rest.partition("\x1f")
        reading["commits"].append(
            {"sha": sha, "subject": subject, "committed_at": int(committed or 0)}
        )
    return reading


__all__ = ["BRANCH_PREFIX", "WORKTREE_KEY", "Worktree", "WorktreeError", "facts", "recorded", "release", "resolve"]
