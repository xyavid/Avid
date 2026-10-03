"""Session-layer exception hierarchy: one class per recovery action, since a bare "it failed" names no next step."""

from __future__ import annotations

from typing import Any


class SessionError(Exception):
    """Base class for every expected failure in the session layer."""


class SessionNotFoundError(SessionError):
    """No session carries the requested id."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"没有这个会话：{session_id}。用 --list-sessions 看看有哪些。")


class SessionExistsError(SessionError):
    """The requested id is already taken."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"会话 id 已存在：{session_id}。换一个 id，或直接续接它。")


class SessionAlreadyOpenError(SessionError):
    """An id may have only one open handle at a time, and one is already held."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"会话已经打开：{session_id}。先关掉现有句柄再打开。")


class SessionClosedError(SessionError):
    """The handle, repository or storage is closed and accepts no further operations."""


class SessionBusyError(SessionError):
    """A mutation callback tried to mutate or close again from the same thread, which self-deadlocks."""


class SessionLockedError(SessionError):
    """Another process holds the session file; concurrent writers would emit duplicate seq values and corrupt replay."""

    def __init__(self, label: str) -> None:
        super().__init__(
            f"会话被另一个进程占用（{label}）：同一会话同一时刻只允许一个进程写入。"
            "等那个进程结束再试，不要用两个进程同时写同一个会话。"
        )


class SessionInvariantError(SessionError):
    """Persisted state contradicts itself (missing parent, duplicate id, unknown branch), so the session cannot advance."""


class SessionStorageError(SessionError):
    """Storage is unusable or malformed: a corrupt file, an unknown version, or a failed read or write."""


class SessionInvalidIdError(SessionError):
    """The session id is blank or contains a path separator, so it cannot be turned into a filename safely."""

    def __init__(self, session_id: Any, reason: str) -> None:
        super().__init__(f"会话 id 非法：{session_id!r}（{reason}）")
        self.session_id = session_id
        self.reason = reason


class SessionInvalidBranchError(SessionError):
    """The branch name is not usable."""

    def __init__(self, branch: str, reason: str) -> None:
        super().__init__(f"分支名非法：{branch!r}（{reason}）")
        self.branch = branch
        self.reason = reason


class SessionBranchExistsError(SessionError):
    """The branch head already exists and is never rebuilt, so a duplicate create would silently drop one chain."""

    def __init__(self, branch: str) -> None:
        super().__init__(f"分支已存在：{branch}。用 branch() 取它，不要重复创建。")


class SessionUnknownTargetError(SessionError):
    """The entry a new branch would start from does not exist."""

    def __init__(self, target_id: str) -> None:
        super().__init__(f"找不到条目：{target_id}。分支起点必须是已经存在的条目。")


class SessionInvalidMessageError(SessionError):
    """A message is not a recordable entry and is rejected before it can reach the session file."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"消息无法写入会话：{reason}")
