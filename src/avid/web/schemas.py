"""线格式：pydantic DTO + 显式 mapper + 错误信封。

两条纪律（设计文档 §6.2）：

* **DTO 由显式 mapper 构造**，字段名与内核一致；JSONL 里已经是 camelCase 的
  字段（``parentId``/``storageVersion``）在 API 里保持同形，避免同一概念两种拼写。
* **HTTP 错误码只承担传输与生命周期语义**；业务失败（工具返回"错误：…"）不是
  HTTP 错误，它以 ``tool_call_finished{status:"failed"}`` 出现。

``classify_tool_status`` 是那条「失败只能由内容前缀判定」规则的**单点**，写在这里
受测（B 系列），并在设计文档 §17 记为待替换项：给 ``ToolOutcome`` 加结构化
``status`` 字段时删掉它。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..runtime import events
from ..runtime.events import RunEvent

# 项目既有约定：工具失败回文本、不抛异常，文本以「错误：」或「工具 X 执行失败：」开头。
_FAILED_PREFIX = "错误："
_FAILED_TOOL_MARK = "执行失败："


# ---------------- 错误信封 ----------------


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorOut(BaseModel):
    error: ErrorBody


# ---------------- 元信息 ----------------


class SkillOut(BaseModel):
    name: str
    description: str


class BuildInfo(BaseModel):
    git_sha: str | None = None
    built_at: str | None = None
    source: str = "dev"


class Capabilities(BaseModel):
    tools: list[str]
    skills: list[SkillOut]
    model: str | None = None
    workspace: str


class StreamInfo(BaseModel):
    heartbeat_seconds: float
    terminal_fallback_seconds: float
    replay_buffer_size: int


class MetaOut(BaseModel):
    api_version: int
    features: dict[str, int]
    event_types: list[str]
    capabilities: Capabilities
    stream: StreamInfo
    build: BuildInfo


class HealthOut(BaseModel):
    status: str
    api_version: int
    uptime_ms: int


# ---------------- 会话与条目 ----------------


class WorkspaceRef(BaseModel):
    """会话归属的线格式。``id`` 来自会话 header（创建时的静态事实）。"""

    id: str | None = None
    root: str | None = None
    name: str | None = None
    default_permission: str | None = None


class WorkspaceOut(WorkspaceRef):
    id: str
    created_at: int = 0
    last_used_at: int = 0
    is_default: bool = False


class WorkspaceListOut(BaseModel):
    workspaces: list[WorkspaceOut]


class CreateWorkspaceIn(BaseModel):
    """登记一个工作区；``permission`` 是它的默认权限模式。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    name: str | None = None
    permission: str | None = None


class SessionSummary(BaseModel):
    id: str
    name: str | None = None
    created_at: int
    storage_version: int
    parent_session_id: str | None = None
    workspace: WorkspaceRef | None = None
    message_count: int
    active_run_id: str | None = None
    truncated_tail: bool = False


class SessionDetail(SessionSummary):
    branch: str = "main"


class SessionListOut(BaseModel):
    sessions: list[SessionSummary]


class CreateSessionIn(BaseModel):
    """``workspace`` 在多工作区模式下必填（服务端 400 `workspace_required`）。

    ``extra="forbid"`` 是刻意的：pydantic 默认**静默丢弃**未知字段，于是"前端加了
    字段、服务端漏加"会变成一个不报错却按默认值跑的模式错配。这里让它变 422。
    """

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    workspace: str | None = None


class RenameSessionIn(BaseModel):
    name: str


class EntryOut(BaseModel):
    entry_id: str
    parent_id: str | None = None
    seq: int
    timestamp: int
    type: str
    message: dict[str, Any] | None = None


class EntryPageOut(BaseModel):
    session_id: str
    branch: str
    order: str
    limit: int
    entries: list[EntryOut]
    has_more: bool
    next_cursor: int | None = None
    truncated_tail: bool = False


class BranchOut(BaseModel):
    name: str
    tip_entry_id: str | None = None
    entry_count: int = 0
    is_default: bool = False


class BranchListOut(BaseModel):
    session_id: str
    branches: list[BranchOut]


class CreateBranchIn(BaseModel):
    """``at`` 是分叉点条目 id；缺省 = 从零开一条空分支。"""

    name: str | None = None
    at: str | None = None


# ---------------- 运行与审批 ----------------


class StartRunIn(BaseModel):
    """``permission`` 缺省按会话所属工作区的默认权限（再缺省才是 strict）。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str
    auto_approve: bool = False
    branch: str = "main"
    permission: Literal["strict", "workspace", "system"] | None = None


class RunCreatedOut(BaseModel):
    run_id: str
    session_id: str
    status: str


class ApprovalOut(BaseModel):
    approval_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    created_at: int
    expires_at: int
    decision: str | None = None
    resolved_at: int | None = None
    resolved_reason: str | None = None


class RunOut(BaseModel):
    run_id: str
    session_id: str
    status: str
    started_at: int
    finished_at: int | None = None
    round: int = 0
    tokens: int = 0
    error: dict[str, Any] | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = None
    pending_approvals: list[ApprovalOut] = Field(default_factory=list)


class CancelOut(BaseModel):
    run_id: str
    status: str
    cancel_requested: bool


class ApprovalListOut(BaseModel):
    approvals: list[ApprovalOut]


class AnswerApprovalIn(BaseModel):
    decision: Literal["allow", "deny"]


class AnswerApprovalOut(BaseModel):
    accepted: bool
    decision: str
    already: str | None = None
    reason: str
    approval_id: str


# ---------------- 任务 ----------------


class TaskOut(BaseModel):
    id: str
    subject: str
    description: str
    status: str
    owner: str | None = None
    blockedBy: list[str] = Field(default_factory=list)
    can_start: bool = False
    blocked: bool = False
    incomplete_dependencies: list[str] = Field(default_factory=list)
    dependency_titles: dict[str, str] = Field(default_factory=dict)


class TaskListOut(BaseModel):
    tasks: list[TaskOut]


class SkillListOut(BaseModel):
    skills: list[SkillOut]


# ---------------- 事件帧 ----------------


class EventPayload(BaseModel):
    run_id: str
    session_id: str
    seq: int | None = None
    ts: int
    type: str
    data: dict[str, Any]


def classify_tool_status(
    content: Any, *, truncated: bool = False, denied_kind: str | None = None
) -> str:
    """``ok | denied | failed | truncated`` 的唯一判定点（§6.2）。

    优先级：拒绝 > 失败 > 截断 > 正常。拒绝与截断直接来自 hook context；失败只能
    由内容前缀判定——这是**一次可接受的临时手段**，替换路径是给 ``ToolOutcome``
    加结构化字段（设计文档 §17.7）。
    """
    if denied_kind:
        return "denied"
    if isinstance(content, str) and (
        content.startswith(_FAILED_PREFIX)
        or _FAILED_TOOL_MARK in content[:64]
    ):
        return "failed"
    if truncated:
        return "truncated"
    return "ok"


def event_payload(event: RunEvent, session_id: str) -> dict[str, Any]:
    """内核事件 → 线上载荷。

    两处加工在这里完成，所以 ``svc/`` 不必认识线格式：

    * ``tool_call_finished`` 丢掉全文、给出 ``status`` 与 ``content_chars``
      （全文已经由 durable 的 ``tool_result_message`` 提供，前端不重复拿）；
    * ``tool_call_denied`` 补上 ``status="denied"``。
    """
    data = dict(event.data)
    if event.type == events.TOOL_CALL_FINISHED:
        content = data.pop("content", "")
        data["status"] = classify_tool_status(
            content, truncated=bool(data.get("truncated"))
        )
        data["content_chars"] = len(content) if isinstance(content, str) else 0
    elif event.type == events.TOOL_CALL_DENIED:
        data["status"] = classify_tool_status("", denied_kind=str(data.get("kind") or "user"))
    return {
        "run_id": event.run_id,
        "session_id": session_id,
        "seq": event.seq,
        "ts": event.ts,
        "type": event.type,
        "data": data,
    }


__all__ = [
    "AnswerApprovalIn",
    "AnswerApprovalOut",
    "ApprovalListOut",
    "ApprovalOut",
    "BuildInfo",
    "CancelOut",
    "Capabilities",
    "CreateSessionIn",
    "EntryOut",
    "EntryPageOut",
    "ErrorBody",
    "ErrorOut",
    "EventPayload",
    "HealthOut",
    "MetaOut",
    "RenameSessionIn",
    "RunCreatedOut",
    "RunOut",
    "SessionDetail",
    "SessionListOut",
    "CreateWorkspaceIn",
    "SessionSummary",
    "WorkspaceListOut",
    "WorkspaceOut",
    "WorkspaceRef",
    "SkillListOut",
    "SkillOut",
    "StartRunIn",
    "StreamInfo",
    "TaskListOut",
    "TaskOut",
    "classify_tool_status",
    "event_payload",
]
