"""Wire format: pydantic DTOs, explicit mappers and the error envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent.events import TOOL_CALL_DENIED, TOOL_CALL_FINISHED, RunEvent
from ..attachments import MAX_BASE64_CHARS

# Tool failures arrive as text, so prefixes separate business, argument and environment errors.
_FAILED_PREFIXES = ("错误：", "参数错误：")
_FAILED_TOOL_MARK = "执行失败："


# Input caps keep one request from writing an unbounded string into session files and memory.
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


class ModelCandidate(BaseModel):
    """A BYOK candidate for a per-run model override: ``ref`` is providerId/modelId, and empty
    ``reasoning_efforts`` means the request carries no effort parameter.
    """

    ref: str = Field(max_length=MAX_NAME_CHARS)
    label: str = Field(max_length=MAX_NAME_CHARS)
    reasoning_efforts: list[str] = Field(default_factory=list)


class Capabilities(BaseModel):
    tools: list[str]
    skills: list[SkillOut]
    model: str | None = None
    # Per-run override candidates as providerId/modelId BYOK refs; empty when BYOK is unconfigured.
    models: list[ModelCandidate] = Field(default_factory=list)
    workspace: str
    # Declared because the response model drops undeclared keys, which would read as absent.
    workspace_picker: str | None = None
    # Sandbox probe results, declared for the same reason: omission would report no sandbox at all.
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
    """Wire form of a session's workspace; its id is fixed in the session header."""

    id: str | None = None
    root: str | None = None
    name: str | None = None


class WorkspaceOut(WorkspaceRef):
    id: str
    created_at: int = 0
    last_used_at: int = 0
    is_default: bool = False


class FileEntryOut(BaseModel):
    """One directory entry; ``path`` is relative to the workspace root (POSIX separators)."""

    name: str
    path: str
    kind: str
    size: int | None = None


class FileListOut(BaseModel):
    path: str
    parent: str | None = None
    entries: list[FileEntryOut]
    #: Set when the listing was cut at the cap, so the panel can say there is more.
    truncated: bool = False


class FileContentOut(BaseModel):
    """Preview of one file; a binary file carries no text at all (``text`` is None)."""

    path: str
    size: int
    text: str | None = None
    binary: bool = False
    truncated: bool = False


class WorkspaceListOut(BaseModel):
    workspaces: list[WorkspaceOut]


class PickFolderOut(BaseModel):
    """Result of the system folder picker; a null path means the user cancelled, not an error."""

    path: str | None = None


class CreateWorkspaceIn(BaseModel):
    """Registration payload: a directory plus an optional display name."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=MAX_PATH_CHARS)
    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)


class CapabilityFlags(BaseModel):
    """Declared model capabilities; None means undeclared, and only an explicit false blocks."""

    model_config = ConfigDict(extra="forbid")

    tool_calling: bool | None = None
    vision: bool | None = None
    json_mode: bool | None = None
    streaming: bool | None = None
    reasoning: bool | None = None


class ByokModel(BaseModel):
    """One model declaration; the read and write forms share this shape."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=MAX_NAME_CHARS)
    label: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    context_window: int | None = Field(default=None, ge=1)
    max_output: int | None = Field(default=None, ge=1)
    # Reasoning levels this model accepts; empty means the parameter is not sent.
    reasoning_efforts: list[str] = Field(default_factory=list)
    capabilities: CapabilityFlags = Field(default_factory=CapabilityFlags)


class ByokProviderIn(BaseModel):
    """One provider endpoint; ``api_key`` is write-only and lands in secrets.json, and auth is
    implicit — a stored key means the protocol's standard header, none means no auth header.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=MAX_ID_CHARS, pattern=r"^[a-z0-9-]+$")
    label: str = Field(max_length=MAX_NAME_CHARS)
    protocol: Literal["openai-compatible", "anthropic", "responses", "ollama"]
    base_url: str = Field(max_length=MAX_PATH_CHARS)
    headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    models: list[ByokModel] = Field(default_factory=list)
    api_key: str | None = Field(default=None, max_length=MAX_PATH_CHARS)


class ByokProviderOut(BaseModel):
    """Echo form: the input shape minus ``api_key`` plus a ``key_set`` boolean, with ``protocol``
    widened to str because the values already passed validation.
    """

    id: str
    label: str
    protocol: str
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    models: list[ByokModel] = Field(default_factory=list)
    key_set: bool = False


class ByokSettingsIn(BaseModel):
    """Whole-config save: the full provider list plus the chat binding, validated before disk."""

    model_config = ConfigDict(extra="forbid")

    providers: list[ByokProviderIn] = Field(default_factory=list)
    bindings: dict[str, str | None] = Field(default_factory=dict)


class ByokSettingsOut(BaseModel):
    """The only source of model connections; with no file, providers is empty and chat is null."""

    providers: list[ByokProviderOut] = Field(default_factory=list)
    bindings: dict[str, str | None] = Field(default_factory=dict)


class ByokTestIn(BaseModel):
    """Connectivity-check payload carrying a full provider declaration that need not be saved."""

    model_config = ConfigDict(extra="forbid")

    provider: ByokProviderIn
    model_id: str = Field(max_length=MAX_NAME_CHARS)


class VerifyStepOut(BaseModel):
    step: str
    ok: bool
    detail: str


class ByokTestOut(BaseModel):
    ok: bool
    steps: list[VerifyStepOut] = Field(default_factory=list)


class SessionsDirOut(BaseModel):
    """Effective session directory; ``editable`` is false when the environment variable wins."""

    dir: str
    default_dir: str
    source: Literal["env", "settings", "default"]
    editable: bool


class SessionsDirIn(BaseModel):
    """Sets the session directory; an empty string restores the default, and nothing is moved."""

    model_config = ConfigDict(extra="forbid")

    dir: str = Field(max_length=MAX_PATH_CHARS)


class SearchHitOut(BaseModel):
    """One content hit: enough to show a snippet and jump back to the raw entry."""

    session_id: str
    entry_id: str
    seq: int
    entry_type: str
    role: str | None = None
    timestamp: int | None = None
    snippet: str
    title: str | None = None
    workspace_id: str | None = None
    workspace_name: str | None = None
    byte_offset: int
    byte_length: int


class SearchResultOut(BaseModel):
    """Search results; ``behind`` is how many sessions the index still lags behind."""

    hits: list[SearchHitOut] = Field(default_factory=list)
    behind: int = 0


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


class ScratchIn(BaseModel):
    """Scratch-session payload; the name is optional and the server derives one."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)


class ScratchOut(SessionDetail):
    """Scratch view: the session plus how many messages were copied into it."""

    copied_messages: int = 0


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
    """One page of entries, where the cursor is exclusive and a truncated tail means older
    entries were dropped."""

    session_id: str
    branch: str
    order: str
    limit: int
    entries: list[EntryOut]
    has_more: bool
    next_cursor: int | None = None
    truncated_tail: bool = False


# Field names come from RunState.usage_report and are compared against the frontend types.
class ContextPartsOut(BaseModel):
    """Estimated token share of each context block, split by character proportion and summing
    to the total."""

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
    """Cache read/write counts with the hit ratio; only Anthropic-style providers report writes."""

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
    # Usage snapshot of the branch's latest run, persisted with the session; null means never run.
    usage: UsageOut | None = None


class BranchListOut(BaseModel):
    session_id: str
    branches: list[BranchOut]


class CreateBranchIn(BaseModel):
    """Branch fork point; an absent entry id starts an empty branch instead of copying one."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    at: str | None = Field(default=None, max_length=MAX_ID_CHARS)


class ImageIn(BaseModel):
    """One outbound image — raw base64 plus an optional name — screened by max_length here only;
    attachments decides type and limits from the bytes, not from the declarations.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    data: str = Field(max_length=MAX_BASE64_CHARS)


class StartRunIn(BaseModel):
    """Run request; ``full_access_ack=true`` is the grant for full access, the only mode there."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    # Images sent with the message: same user message, text first, then images in array order.
    images: list[ImageIn] = Field(default_factory=list)
    # Claim a queued input: its content and switches win, so prompt/images here are ignored.
    from_input: str | None = Field(default=None, max_length=MAX_ID_CHARS)
    auto_approve: bool = False
    branch: str = Field(default="main", max_length=MAX_NAME_CHARS)
    # Per-run model override; absent or empty falls back to the configured chat binding.
    model: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    # Per-run reasoning effort; must be among the chosen model's levels (the kernel enforces it).
    reasoning_effort: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    full_access_ack: bool = False


class InputIn(BaseModel):
    """One supplemental input: ``client_id`` is the idempotency key for client retries, and
    ``mode`` is the earliest moment it may take effect — ``now`` (next step of the active run,
    or a run when idle) or ``after`` (the next turn).
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["now", "after"] = "after"
    prompt: str = Field(default="", max_length=MAX_PROMPT_CHARS)
    images: list[ImageIn] = Field(default_factory=list)
    client_id: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    model: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    reasoning_effort: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    branch: str = Field(default="main", max_length=MAX_NAME_CHARS)
    full_access_ack: bool = False
    auto_approve: bool = False


class InputOut(BaseModel):
    """One pending input: text preview and image count; bytes come from the read endpoint."""

    input_id: str
    mode: str
    text: str = ""
    images: int = 0
    client_id: str | None = None
    created_at: int = 0
    missed: bool = False


class InputListOut(BaseModel):
    inputs: list[InputOut]


class InputAcceptedOut(BaseModel):
    """Result: ``kind=run`` means a run just started (stream it); ``input`` stays queued."""

    kind: str
    input_id: str | None = None
    run_id: str | None = None
    mode: str


class RunCreatedOut(BaseModel):
    run_id: str
    session_id: str
    status: str


class ApprovalOut(BaseModel):
    """One pending item; ``kind`` says whether it is a decision (approval) or a question, and both
    kinds share one table, one UI slot and this DTO.
    """

    approval_id: str
    tool: str
    arguments: dict[str, Any]
    reason: str
    created_at: int
    expires_at: int
    kind: str = "approval"
    options: list[str] = Field(default_factory=list)
    decision: str | None = None
    answer: str | None = None
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
    """A decision sends ``decision``, a question sends ``answer``; both or neither is a 422."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["allow", "deny"] | None = None
    answer: str | None = Field(default=None, max_length=MAX_PROMPT_CHARS)


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


#: Chars of a subagent tool result kept on the wire, so a parallel fan-out cannot bloat the stream.
SUBAGENT_CONTENT_CHARS = 4000


def event_payload(event: RunEvent, session_id: str) -> dict[str, Any]:
    """Maps a kernel event to its wire payload, dropping tool output and adding derived status
    fields."""
    data = dict(event.data)
    if event.type == TOOL_CALL_FINISHED:
        content = data.pop("content", "")
        data["status"] = classify_tool_status(content, truncated=bool(data.get("truncated")))
        data["content_chars"] = len(content) if isinstance(content, str) else 0
        if "subagent" in data:
            # A subagent result is never persisted, so the wire is its only channel.
            # Parent-run calls stay status-only; their content arrives as a durable message.
            data["content"] = content[:SUBAGENT_CONTENT_CHARS] if isinstance(content, str) else ""
    elif event.type == TOOL_CALL_DENIED:
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
