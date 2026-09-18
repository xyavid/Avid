"""应用服务的领域错误。

这些错误**只表达业务语义**，不表达传输：``status`` 只是给 ``web/`` 做默认映射的
建议值，``web/`` 也可以覆盖它。好处是 ``svc/`` 不必 import 任何 HTTP 框架
（不变量 A4），而错误码仍是稳定字符串、与事件类型共用一份命名规则（§6.2）。
"""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    """应用服务错误的基类。``code`` 是稳定字符串，前端按它分支。"""

    code = "internal"
    status = 500

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class RunBusy(ServiceError):
    """同一会话已有活动 run（不变量 I3，仅进程内）。"""

    code = "run_busy"
    status = 409


class RunNotFound(ServiceError):
    code = "run_not_found"
    status = 404


class RunFinished(ServiceError):
    """对已经结束的 run 请求取消。"""

    code = "run_already_finished"
    status = 409


class SessionNotFound(ServiceError):
    code = "session_not_found"
    status = 404


class SessionExists(ServiceError):
    code = "session_exists"
    status = 409


class SessionBusy(ServiceError):
    """删除一个仍有活动 run 的会话。"""

    code = "session_busy"
    status = 409


class BranchExists(ServiceError):
    """同名分支已存在。悄悄重建会丢掉原来那条链，所以显式报错。"""

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
    """已决审批收到一个**不同**的答复。同一答复重复投递走 200 + accepted:false。"""

    code = "approval_resolved"
    status = 409


class TaskNotFound(ServiceError):
    code = "task_not_found"
    status = 404


class TaskCorrupt(ServiceError):
    code = "task_corrupt"
    status = 500


class SessionReadError(ServiceError):
    """会话文件损坏或读不了。列表路径跳过坏项，单会话路径显式失败。"""

    code = "session_error"
    status = 500


__all__ = [
    "ApprovalConflict",
    "ApprovalExpired",
    "ApprovalNotFound",
    "BranchExists",
    "InvalidRequest",
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
]


class WorkspaceExists(ServiceError):
    """要登记的工作区已经在列表里（进程绑定的或已登记的）。"""

    code = "workspace_exists"
    status = 409


class PickerBusy(ServiceError):
    """已经有一个文件夹选择器开着。"""

    code = "picker_busy"
    status = 409


class PickerUnavailable(ServiceError):
    """这台机器上没有可用的系统文件夹选择器。"""

    code = "picker_unavailable"
    status = 503


class PickerFailed(ServiceError):
    """选择器后端起得来但失败了。"""

    code = "picker_failed"
    status = 500
