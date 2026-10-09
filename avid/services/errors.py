"""Domain errors of the application services, carrying stable machine-readable error codes."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """Base class for service errors; ``code`` is the stable string the client branches on."""

    # Suggested defaults for the wire format; the transport layer may override the status.
    code = "internal"
    status = 500

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class RunBusy(ServiceError):
    """The session already has an active run inside this process."""

    code = "run_busy"
    status = 409


class RunNotFound(ServiceError):
    code = "run_not_found"
    status = 404


class RunFinished(ServiceError):
    """Cancellation was requested for a run that has already ended."""

    code = "run_already_finished"
    status = 409


class SessionNotFound(ServiceError):
    code = "session_not_found"
    status = 404


class SessionExists(ServiceError):
    code = "session_exists"
    status = 409


class SessionBusy(ServiceError):
    """An attempt to delete a session that still has an active run."""

    code = "session_busy"
    status = 409


class BranchExists(ServiceError):
    """A branch of that name exists; recreating it would silently drop the old chain."""

    code = "branch_exists"
    status = 409


class SearchUnavailable(ServiceError):
    """The derived index cannot be used in this process; sessions themselves are unaffected."""

    code = "search_unavailable"
    status = 503


class InvalidRequest(ServiceError):
    code = "invalid_request"
    status = 400


class ApprovalNotFound(ServiceError):
    code = "approval_not_found"
    status = 404


class ApprovalExpired(ServiceError):
    code = "approval_expired"
    status = 410


class ApprovalConflict(ServiceError):
    """A settled approval received a different answer; the same answer repeated is idempotent."""

    code = "approval_resolved"
    status = 409



class SessionReadError(ServiceError):
    """A session file is corrupt or unreadable; listings skip it, single-session reads fail."""

    code = "session_error"
    status = 500


class AttachmentRejected(ServiceError):
    """一条图片附件不合法（类型 / 单图大小 / 张数 / base64）：客户端改完再发。

    单独一类而不是并进 invalid_request：界面上的恢复动作不同——这条要么换图、
    要么压缩，而不是改参数。
    """

    code = "invalid_attachment"
    status = 400


class WorkspaceExists(ServiceError):
    """The workspace to register is already listed, either bound to the process or registered."""

    code = "workspace_exists"
    status = 409


class PickerBusy(ServiceError):
    """A folder picker dialog is already open."""

    code = "picker_busy"
    status = 409


class PickerUnavailable(ServiceError):
    """This machine has no usable system folder picker."""

    code = "picker_unavailable"
    status = 503


class PickerFailed(ServiceError):
    """A picker backend started but then failed."""

    code = "picker_failed"
    status = 500


class FileOutside(ServiceError):
    """浏览路径越出工作区（含 `..` 与 symlink 穿透）。"""

    code = "file_outside"
    status = 403


class FileSensitive(ServiceError):
    """凭据类路径不开放浏览；判据与工具读文件同一道闸。"""

    code = "file_sensitive"
    status = 403


class FileNotDirectory(ServiceError):
    code = "file_not_directory"
    status = 400


class FileMissing(ServiceError):
    """目标文件或目录不存在（含读不动：权限、竞态删除）。"""

    code = "file_missing"
    status = 404


class TooManyStreams(ServiceError):
    """Too many event streams are open; the cap is the guard rail published by the service layer."""

    code = "too_many_streams"
    status = 503


__all__ = [
    "ApprovalConflict",
    "ApprovalExpired",
    "ApprovalNotFound",
    "AttachmentRejected",
    "BranchExists",
    "FileMissing",
    "FileNotDirectory",
    "FileOutside",
    "FileSensitive",
    "InvalidRequest",
    "PickerBusy",
    "PickerFailed",
    "PickerUnavailable",
    "RunBusy",
    "RunFinished",
    "RunNotFound",
    "ServiceError",
    "SessionBusy",
    "SessionExists",
    "SessionNotFound",
    "SessionReadError",
    "TooManyStreams",
    "WorkspaceExists",
]
