"""Wire format: pydantic DTOs, explicit mappers and the error envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..policy.permission import full_grant_error
from ..runtime import events
from ..runtime.events import RunEvent

# Tool failures come back as text, so three prefixes separate business, argument and environment errors.
_FAILED_PREFIXES = ("错误：", "参数错误：")
_FAILED_TOOL_MARK = "执行失败："


# Input length caps keep a single request from writing an unbounded string into session files and memory.
MAX_PATH_CHARS = 4096
MAX_NAME_CHARS = 200
MAX_ID_CHARS = 200
# 1 MB: roomy for a long paste, still small enough that one request cannot exhaust the process.
MAX_PROMPT_CHARS = 1_000_000


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorOut(BaseModel):
    error: ErrorBody


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
    # Declared because the response model silently drops undeclared keys, which would read as absent.
    workspace_picker: str | None = None
    # Sandbox probe results, declared for the same reason: an omission would report no sandbox at all.
    sandbox: dict[str, Any] = Field(default_factory=dict)


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


class WorkspaceRef(BaseModel):
    """Wire form of a session's workspace, whose id is a static fact recorded in the session header."""

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


class PickFolderOut(BaseModel):
    """Result of the system folder picker, where a null path means the user cancelled and not an error."""

    path: str | None = None


class CreateWorkspaceIn(BaseModel):
    """Registration payload: a directory plus the default permission mode it may never set to full."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=MAX_PATH_CHARS)
    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    # An enum, so an invalid mode is a 422 instead of a file written before the failure surfaces.
    permission: Literal["manual", "auto"] | None = None


class ModelSettingsOut(BaseModel):
    """Effective model connection for the settings UI; the API key is never returned."""

    model: str | None = None
    base_url: str | None = None
    # None = 按 base_url 自动识别协议族（config.detect_provider）
    provider: str | None = None
    api_key_set: bool = False
    # True = 界面覆盖层（~/.avid/model.toml）存在且优先于环境变量
    overlay_active: bool = False


class ModelSettingsIn(BaseModel):
    """UI overlay payload: every field optional; an empty string clears the field back to env."""

    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    base_url: str | None = Field(default=None, max_length=MAX_PATH_CHARS)
    # An enum, so an invalid provider is a 422 instead of a file written before the failure surfaces.
    provider: Literal["openai", "anthropic", "gemini"] | None = None
    api_key: str | None = Field(default=None, max_length=MAX_PATH_CHARS)


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
    """Creation payload; an unknown field is rejected rather than silently dropped by pydantic."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=MAX_ID_CHARS)
    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    workspace: str | None = Field(default=None, max_length=MAX_ID_CHARS)


class RenameSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=MAX_NAME_CHARS)


class EntryOut(BaseModel):
    entry_id: str
    parent_id: str | None = None
    seq: int
    timestamp: int
    type: str
    message: dict[str, Any] | None = None


class EntryPageOut(BaseModel):
    """One page of entries, where the cursor is exclusive and a truncated tail means older entries went."""

    session_id: str
    branch: str
    order: str
    limit: int
    entries: list[EntryOut]
    has_more: bool
    next_cursor: int | None = None
    truncated_tail: bool = False


# Field names are decided by RunState.usage_report and compared against the frontend type definitions.
class ContextPartsOut(BaseModel):
    """Estimated token share of each context block, split by character proportion, summing to the total."""

    system: int = 0
    tools: int = 0
    messages: int = 0


class ContextUsageOut(BaseModel):
    """Context occupancy, where a null value means the figure is unknown and the UI shows a dash."""

    tokens: int | None = None
    window: int | None = None
    utilization: float | None = None
    # Null means no block breakdown yet, so the UI draws no stacked bar.
    parts: ContextPartsOut | None = None


class CacheUsageOut(BaseModel):
    """Cache read and write counts with the hit ratio; only Anthropic-style providers report writes."""

    read_tokens: int | None = None
    write_tokens: int | None = None
    hit_ratio: float | None = None


class CompactionUsageOut(BaseModel):
    """Compaction count and the prompt size of the first round that followed the last compaction."""

    count: int = 0
    last_compaction_tokens: int | None = None
    last_step: str | None = None


class UsageOut(BaseModel):
    context: ContextUsageOut
    cache: CacheUsageOut
    compaction: CompactionUsageOut


class BranchOut(BaseModel):
    name: str
    tip_entry_id: str | None = None
    entry_count: int = 0
    is_default: bool = False
    # Usage snapshot of the branch's most recent run, persisted with the session; null means never run.
    usage: UsageOut | None = None


class BranchListOut(BaseModel):
    session_id: str
    branches: list[BranchOut]


class CreateBranchIn(BaseModel):
    """Branch fork point; an absent entry id starts an empty branch instead of copying an existing one."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    at: str | None = Field(default=None, max_length=MAX_ID_CHARS)


class StartRunIn(BaseModel):
    """Run request; full permission without the acknowledgement flag is rejected by validation."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(max_length=MAX_PROMPT_CHARS)
    auto_approve: bool = False
    branch: str = Field(default="main", max_length=MAX_NAME_CHARS)
    permission: Literal["manual", "auto", "full"] | None = None
    full_access_ack: bool = False

    @model_validator(mode="after")
    def _full_needs_ack(self) -> "StartRunIn":
        problem = full_grant_error(
            self.permission, acknowledged=self.full_access_ack, source="web"
        )
        if problem is not None:
            raise ValueError(problem)
        return self


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
    # Model rounds completed so far.
    round: int = 0
    tokens: int = 0
    # Latest snapshot in the unified usage schema; null means no reading yet.
    usage: UsageOut | None = None
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


class SkillListOut(BaseModel):
    skills: list[SkillOut]


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
    """Single decision point for ok, denied, failed and truncated, in that order of precedence."""
    if denied_kind:
        return "denied"
    # Denial comes from the hook context, while failure can only be read off the content prefix.
    if isinstance(content, str) and (
        content.startswith(_FAILED_PREFIXES) or _FAILED_TOOL_MARK in content[:64]
    ):
        return "failed"
    if truncated:
        return "truncated"
    return "ok"


def event_payload(event: RunEvent, session_id: str) -> dict[str, Any]:
    """Maps a kernel event to its wire payload, dropping tool output and adding derived status fields."""
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
    "CacheUsageOut",
    "CancelOut",
    "Capabilities",
    "CompactionUsageOut",
    "ContextPartsOut",
    "ContextUsageOut",
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
    "PickFolderOut",
    "SessionSummary",
    "WorkspaceListOut",
    "WorkspaceOut",
    "WorkspaceRef",
    "SkillListOut",
    "SkillOut",
    "StartRunIn",
    "StreamInfo",
    "UsageOut",
    "classify_tool_status",
    "event_payload",
]
