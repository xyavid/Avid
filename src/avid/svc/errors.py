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


class TooManyStreams(ServiceError):
    """Too many event streams are open; the cap is the guard rail published by the service layer."""

    code = "too_many_streams"
    status = 503


__all__ = [
    "ApprovalConflict",
    "ApprovalExpired",
    "ApprovalNotFound",
    "BranchExists",
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
    "TaskCorrupt",
    "TaskNotFound",
    "TooManyStreams",
    "WorkspaceExists",
]
