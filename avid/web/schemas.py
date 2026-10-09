"""Wire format: pydantic DTOs, explicit mappers and the error envelope."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..agent.events import TOOL_CALL_DENIED, TOOL_CALL_FINISHED, RunEvent

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


class ModelCandidate(BaseModel):
    """按运行换模型的 BYOK 候选：`ref` 是 providerId/modelId，label 是展示名。

    `reasoning_efforts` 是这个模型声明的推理强度档位（界面据此列出可选项）；空 = 不提。
    """

    ref: str = Field(max_length=MAX_NAME_CHARS)
    label: str = Field(max_length=MAX_NAME_CHARS)
    reasoning_efforts: list[str] = Field(default_factory=list)


class Capabilities(BaseModel):
    tools: list[str]
    skills: list[SkillOut]
    model: str | None = None
    # 可切换的模型候选（本次运行的覆盖用）；取自内核的窗口表，不是提供商目录。
    # BYOK 候选（providerId/modelId ref）；不预置模型选项，没配 BYOK 时为空。
    models: list[ModelCandidate] = Field(default_factory=list)
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
    #: 条目数超出上限时截断并置真（面板要能说"还有更多"）。
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
    """Result of the system folder picker, where a null path means the user cancelled and not an error."""

    path: str | None = None


class CreateWorkspaceIn(BaseModel):
    """Registration payload: a directory plus an optional display name."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=MAX_PATH_CHARS)
    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    # 阶段 51 之后没有权限模式可选；字段移除，注册只带 name。


class CapabilityFlags(BaseModel):
    """模型能力声明；None = 未声明（保守处理：运行期不据此短路，只有显式 false 才拦）。"""

    model_config = ConfigDict(extra="forbid")

    tool_calling: bool | None = None
    vision: bool | None = None
    json_mode: bool | None = None
    streaming: bool | None = None
    reasoning: bool | None = None


class ByokModel(BaseModel):
    """一个具体模型：id + 可选展示名 / 窗口 / 输出上限 / 推理强度档位 / 能力声明（读写同形）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(max_length=MAX_NAME_CHARS)
    label: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    context_window: int | None = Field(default=None, ge=1)
    max_output: int | None = Field(default=None, ge=1)
    # 这个模型认哪些推理强度档位（运行时从列表里挑一个）；空 = 不带这个参数。
    reasoning_efforts: list[str] = Field(default_factory=list)
    capabilities: CapabilityFlags = Field(default_factory=CapabilityFlags)


class ByokProviderIn(BaseModel):
    """一个接入端点：协议 + base URL + 可选密钥。api_key 只入不出，落 secrets.json。

    鉴权隐式：密钥库按 provider id 存了密钥就按协议标准头发送，没存就不带鉴权头
    （本地服务）——没有 auth_type/header_name 这类选择。
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
    """GET 回显：与 In 同形但**没有 api_key**，多一个 key_set 布尔。

    protocol 收宽成 str：值来自已通过 validate 的配置，回显侧不再用
    Literal 收紧一遍（In 侧的 Literal 负责把非法值挡在 422）。
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
    """整体保存：providers 全量 + chat 绑定；服务端先 validate 再落盘。"""

    model_config = ConfigDict(extra="forbid")

    providers: list[ByokProviderIn] = Field(default_factory=list)
    bindings: dict[str, str | None] = Field(default_factory=dict)


class ByokSettingsOut(BaseModel):
    """模型连接的唯一来源；没有文件时 providers 为空、chat 绑定为 null。"""

    providers: list[ByokProviderOut] = Field(default_factory=list)
    bindings: dict[str, str | None] = Field(default_factory=dict)


class ByokTestIn(BaseModel):
    """连通校验载荷：携带**未保存也能测**的完整提供商声明 + 要测的模型 id。"""

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
    """会话目录：当前值、默认值、生效来源；环境变量赢时 editable 为 false（界面只读）。"""

    dir: str
    default_dir: str
    source: Literal["env", "settings", "default"]
    editable: bool


class SessionsDirIn(BaseModel):
    """设置会话目录；空串表示恢复默认。只改配置，不搬已有会话。"""

    model_config = ConfigDict(extra="forbid")

    dir: str = Field(max_length=MAX_PATH_CHARS)


class SearchHitOut(BaseModel):
    """一条内容命中：够显示片段，也够跳回原文（会话 + 条目 + 行偏移）。"""

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
    """检索结果；behind 是「索引还落后多少个会话」——如实说，别让人以为搜全了。"""

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
    """临时会话的创建载荷：名字可省（服务端给「临时对话 · 源名」）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)


class ScratchOut(SessionDetail):
    """临时会话的视图：会话本身 + 拷了多少条消息（界面拿去说「带上了 N 条上下文」）。"""

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
    """Run request; full_access_ack=true 就是完全访问的授予凭据，没有别的模式可选。"""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(max_length=MAX_PROMPT_CHARS)
    auto_approve: bool = False
    branch: str = Field(default="main", max_length=MAX_NAME_CHARS)
    # 本次运行的模型覆盖；缺省 = 按设置（.env + 界面覆盖层）解析。空串按缺省处理。
    model: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    # 本次运行的推理强度：必须在所选模型声明的档位列表里（内核按列表校验）。空串按缺省处理。
    reasoning_effort: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    full_access_ack: bool = False


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


#: 子运行工具结果在线上保留的字符数：够看清它拿到了什么，又不让一次并行派发把事件流撑爆。
SUBAGENT_CONTENT_CHARS = 4000


def event_payload(event: RunEvent, session_id: str) -> dict[str, Any]:
    """Maps a kernel event to its wire payload, dropping tool output and adding derived status fields."""
    data = dict(event.data)
    if event.type == TOOL_CALL_FINISHED:
        content = data.pop("content", "")
        data["status"] = classify_tool_status(content, truncated=bool(data.get("truncated")))
        data["content_chars"] = len(content) if isinstance(content, str) else 0
        if "subagent" in data:
            # 子运行的工具结果**只能走这条线**：它不落库，没有 tool_result_message 那样的
            # durable 通道。所以这里留一段截断的正文（界面的子智能体面板要看到它拿到了什么）；
            # 父运行的调用照旧只报状态与长度——结果由 durable 消息给，线格式不重复搬运。
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
