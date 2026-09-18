/**
 * REST 契约的 TS 形状：与 `src/avid/web/schemas.py` 的 pydantic DTO 一一对应。
 *
 * 为什么手写而不是生成：设计文档 §6.3 的机械检查先做「两侧事件清单一致」，
 * 生成器要等「事件数量 × 变更频率」超过人工同步成本（§16 给了触发信号）。
 * 到时用 OpenAPI 生成到 `src/api/generated/`，本文件就是替换点。
 */

export interface ErrorEnvelope {
  error: { code: string; message: string; detail: Record<string, unknown> }
}

export interface BuildInfo {
  git_sha: string | null
  built_at: string | null
  source: string
}

export interface Skill {
  name: string
  description: string
}

/**
 * 权限三态（阶段 18）：模式名就是信任边界，`strict ⊇ workspace ⊇ system`。
 * 服务端 `StartRunIn.permission` 是 `Literal[...] | None`，非法值 422。
 */
export type PermissionMode = 'strict' | 'workspace' | 'system'

/** 系统文件夹选择器的结果：`path` 为 null = 用户取消（不是错误）。 */
export interface PickFolderResult {
  path: string | null
}

/** 会话归属的线格式（`SessionSummary.workspace`）：`id` 来自会话 header。 */
export interface WorkspaceRef {
  id: string | null
  root: string | null
  name: string | null
  default_permission: PermissionMode | null
}

/**
 * `GET /api/workspaces` 的一项。
 *
 * `is_default` 只有单工作区模式才可能为 true；`name` 与 `default_permission` 在
 * pydantic DTO 里都是可空字段（`WorkspaceOut` 继承 `WorkspaceRef`），所以这里也按可空接。
 */
export interface WorkspaceSummary {
  id: string
  root: string
  name: string | null
  created_at: number
  last_used_at: number
  default_permission: PermissionMode | null
  is_default: boolean
}

export interface Capabilities {
  tools: string[]
  skills: Skill[]
  model: string | null
  workspace: string
  /** 这台机器上会用到哪个文件夹选择器后端（null = 没有可用的）。诊断用。 */
  workspace_picker: string | null
}

export interface StreamInfo {
  heartbeat_seconds: number
  terminal_fallback_seconds: number
  replay_buffer_size: number
}

export interface Meta {
  api_version: number
  features: Record<string, number>
  event_types: string[]
  capabilities: Capabilities
  stream: StreamInfo
  build: BuildInfo
}

export interface Health {
  status: string
  api_version: number
  uptime_ms: number
}

export interface SessionSummary {
  id: string
  name: string | null
  created_at: number
  storage_version: number
  parent_session_id: string | null
  /** 归属的工作区；由会话 header 决定，客户端只读。 */
  workspace: WorkspaceRef | null
  message_count: number
  active_run_id: string | null
  truncated_tail: boolean
}

export interface SessionDetail extends SessionSummary {
  branch: string
}

export interface Entry {
  entry_id: string
  parent_id: string | null
  seq: number
  timestamp: number
  type: string
  message: Record<string, unknown> | null
}

export interface EntryPage {
  session_id: string
  branch: string
  order: 'asc' | 'desc'
  limit: number
  entries: Entry[]
  has_more: boolean
  next_cursor: number | null
  truncated_tail: boolean
}

export interface Approval {
  approval_id: string
  tool: string
  arguments: Record<string, unknown>
  reason: string
  created_at: number
  expires_at: number
  decision: string | null
  resolved_at: number | null
  resolved_reason: string | null
}

export type RunStatus =
  | 'running'
  | 'awaiting_approval'
  | 'finished'
  | 'failed'
  | 'cancelled'

export interface Run {
  run_id: string
  session_id: string
  status: RunStatus
  started_at: number
  finished_at: number | null
  round: number
  tokens: number
  error: { code: string; message: string } | null
  cancel_requested: boolean
  cancel_reason: string | null
  pending_approvals: Approval[]
}

export interface RunCreated {
  run_id: string
  session_id: string
  status: RunStatus
}

export interface CancelResult {
  run_id: string
  status: RunStatus
  cancel_requested: boolean
}

export interface ApprovalAnswer {
  accepted: boolean
  decision: 'allow' | 'deny'
  already: string | null
  reason: string
  approval_id: string
}

export interface Task {
  id: string
  subject: string
  description: string
  status: 'pending' | 'in_progress' | 'completed'
  owner: string | null
  blockedBy: string[]
  can_start: boolean
  blocked: boolean
  incomplete_dependencies: string[]
  dependency_titles: Record<string, string>
}

export interface Branch {
  name: string
  /** 链尾条目 id；空分支为 null。 */
  tip_entry_id: string | null
  entry_count: number
  is_default: boolean
}

export interface BranchList {
  session_id: string
  branches: Branch[]
}

export interface StartRunInput {
  prompt: string
  auto_approve?: boolean
  /** 这次运行接在哪条链尾上；缺省 = main。 */
  branch?: string
  /** 这次运行的权限模式；缺省由服务端按会话所属工作区的默认权限回落。 */
  permission?: PermissionMode
}
