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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..policy.permission import full_grant_error

from ..runtime import events
from ..runtime.events import RunEvent

# 项目既有约定：工具失败回文本、不抛异常。三类前缀分工——`错误：`（业务拒绝）、
# `参数错误：`（参数不合 schema）、`执行失败：`（程序 / 环境错误）。
_FAILED_PREFIXES = ("错误：", "参数错误：")
_FAILED_TOOL_MARK = "执行失败："


# ---------------- 错误信封 ----------------


# 输入长度上限（P2-24）。没有上限时一次请求就能把任意大的字符串写进会话文件与
# 内存，而"读回"要走全量重放、每条消息还要一次 fsync——代价被放大。上限取得很宽
# （正常使用远够），只拦住"明显不是人打出来的"输入。
MAX_PATH_CHARS = 4096
MAX_NAME_CHARS = 200
MAX_ID_CHARS = 200
MAX_PROMPT_CHARS = 1_000_000  # 1 MB：长粘贴够用，又不至于让一次请求吃掉整个进程


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
    # 这台机器上会用到哪个文件夹选择器后端（None = 没有可用的）。
    # **必须声明**：pydantic 的响应模型会静默丢掉未声明的键，于是"后端其实有"会显示成没有。
    workspace_picker: str | None = None
    # 沙箱后端探测结果。同样必须声明，理由同上：漏声明就等于"这台机器没有沙箱"。
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


class PickFolderOut(BaseModel):
    """系统文件夹选择器的结果：``path`` 为 ``None`` 表示用户取消（不是错误）。"""

    path: str | None = None


class CreateWorkspaceIn(BaseModel):
    """登记一个工作区；``permission`` 是它的默认权限模式。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=MAX_PATH_CHARS)
    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    # 枚举而不是自由字符串：非法模式在 schema 层就是 422，不会走到"先落盘再 500"。
    # **没有 full**：工作区默认权限不接受它（full ≠ default），所以它在类型上就不存在。
    permission: Literal["manual", "auto"] | None = None


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
    session_id: str
    branch: str
    order: str
    limit: int
    entries: list[EntryOut]
    has_more: bool
    next_cursor: int | None = None
    truncated_tail: bool = False


# ---------------- 用量台账（阶段 22） ----------------
#
# 形状由 `runtime/state.py` 的 `RunState.usage_report()` 单点决定，这里只是它的
# 线格式。四个模型都进 `test_wire_contract.py` 的 PAIRS：字段名在 pydantic、
# `web/src/api/types.ts` 与服务端真实响应三处机械比对。


class ContextPartsOut(BaseModel):
    """三块文本各占多少 token：**按字符占比**把真实 ``prompt_tokens`` 分配出来的估算。

    三块之和恰好等于 :class:`ContextUsageOut` 的 ``tokens``；界面给每块加 `~`
    并注明是估算。系统提示与工具定义从不发给前端，所以只能由内核算。
    """

    system: int = 0
    tools: int = 0
    messages: int = 0


class ContextUsageOut(BaseModel):
    """上下文占用。None 表示"没有这个数"（没读数 / 不认识该模型的窗口）→ 界面「—」。"""

    tokens: int | None = None
    window: int | None = None
    utilization: float | None = None
    # None = 还没有分块数据（没读数，或这一轮没记字符数）→ 界面不画堆叠条。
    parts: ContextPartsOut | None = None


class CacheUsageOut(BaseModel):
    """缓存读/写与命中率。写计数只有 Anthropic 系会上报，其余为 None。"""

    read_tokens: int | None = None
    write_tokens: int | None = None
    hit_ratio: float | None = None


class CompactionUsageOut(BaseModel):
    """压缩次数与"压完还剩多少"（= 压缩后下一轮的真实 prompt_tokens）。"""

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
    # 该分支最近一次运行的用量快照（会话值，落盘；None = 还没跑过）。
    usage: UsageOut | None = None


class BranchListOut(BaseModel):
    session_id: str
    branches: list[BranchOut]


class CreateBranchIn(BaseModel):
    """``at`` 是分叉点条目 id；缺省 = 从零开一条空分支。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=MAX_NAME_CHARS)
    at: str | None = Field(default=None, max_length=MAX_ID_CHARS)


# ---------------- 运行与审批 ----------------


class StartRunIn(BaseModel):
    """``permission`` 缺省按会话所属工作区的默认权限（再缺省才是 manual）。

    ``permission="full"`` 必须同时给 ``full_access_ack=true``：显式授权是请求体的一部分，
    不是客户端界面的一部分。少了它就 422 —— 服务端不会替用户"理解"这个意图。
    """

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
    round: int = 0
    tokens: int = 0
    # 统一 usage schema 的最近一份快照；None = 还没有读数。
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
        content.startswith(_FAILED_PREFIXES) or _FAILED_TOOL_MARK in content[:64]
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
    "TaskListOut",
    "TaskOut",
    "UsageOut",
    "classify_tool_status",
    "event_payload",
]
