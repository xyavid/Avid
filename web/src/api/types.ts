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

/** 沙箱后端探测结果（`GET /api/meta` 的 `capabilities.sandbox`）。 */
export interface SandboxState {
  backend: string
  available: boolean
  network: boolean
  reason: string | null
  /** 内核 Landlock ABI 版本；只有 ABI ≥ 4 才能强制网络（这里只做诊断展示）。 */
  landlock_abi: number | null
}

/** 系统文件夹选择器的结果：`path` 为 null = 用户取消（不是错误）。 */
export interface PickFolderResult {
  path: string | null
}

/** 会话归属的线格式（`SessionSummary.workspace`）：`id` 来自会话 header。 */
export interface WorkspaceRef {
  id: string | null
  root: string | null
  name: string | null
}

/**
 * `GET /api/workspaces` 的一项。
 *
 * `is_default` 只有单工作区模式才可能为 true；`name` 在 pydantic DTO 里是可空字段
 * （`WorkspaceOut` 继承 `WorkspaceRef`），所以这里也按可空接。
 * 阶段 51 起工作区没有默认权限——权限只按运行给（见 `StartRunInput`）。
 */
export interface WorkspaceSummary {
  id: string
  root: string
  name: string | null
  created_at: number
  last_used_at: number
  is_default: boolean
}

/** 按运行换模型的 BYOK 候选：`ref` 是 providerId/modelId，`label` 是展示名。 */
export interface ModelCandidate {
  ref: string
  label: string
  /** 该模型声明的推理强度档位：输入区的强度选择器按它列选项。 */
  reasoning_efforts?: string[]
}

export interface Capabilities {
  tools: string[]
  skills: Skill[]
  model: string | null
  /** 可切换的模型候选（内核窗口表；不是提供商目录）。 */
  /** BYOK 候选（providerId/modelId ref）；不预置模型选项，没配 BYOK 时为空。 */
  models: ModelCandidate[]
  workspace: string
  /** 这台机器上会用到哪个文件夹选择器后端（null = 没有可用的）。诊断用。 */
  workspace_picker: string | null
  /** 沙箱后端探测结果：界面据此说清"沙箱到底在不在"，而不是照模式猜。 */
  sandbox: SandboxState
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

/**
 * 临时会话（`POST /api/sessions/{id}/scratch`）：从源会话拷了一份上下文、带只读标记，
 * 界面离开面板即销毁。`copied_messages` 是拷了多少条——面板拿去说「带上了 N 条上下文」。
 */
export interface ScratchSession extends SessionDetail {
  copied_messages: number
}

export interface Entry {
  entry_id: string
  parent_id: string | null
  seq: number
  timestamp: number
  type: string
  message: Record<string, unknown> | null
}

/** 工作区文件浏览（`GET /api/workspaces/{id}/files`）的一项。 */
export interface FileEntry {
  name: string
  /** 相对工作区根（POSIX 分隔）。 */
  path: string
  kind: 'dir' | 'file'
  /** 目录没有大小（要递归才知道）。 */
  size: number | null
}

export interface FileList {
  path: string
  /** 上一级相对路径；根目录为 null。 */
  parent: string | null
  entries: FileEntry[]
  truncated: boolean
}

/** 文件预览：二进制文件没有 text（只报事实，不猜编码）。 */
export interface FileContent {
  path: string
  size: number
  text: string | null
  binary: boolean
  truncated: boolean
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
  /** 最近一份 usage 快照；null = 还没有读数。 */
  usage: UsageReport | null
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

/**
 * 统一 usage 快照（阶段 22）——与 `RunState.usage_report()`、服务端 `UsageOut` 同形。
 *
 * 可空字段的 `null` 一律表示**没有这个数**（端点没上报用量 / 不认识该模型的窗口 /
 * 这家没有写入缓存的计数），界面显示「—」；不要当 0 渲染，"未上报"与"确实为 0"不同。
 */
/** 三块文本的**估算** token（按字符占比分配真实总数，三块之和 = tokens）。 */
export interface ContextParts {
  system: number
  tools: number
  messages: number
}

export interface ContextUsage {
  tokens: number | null
  window: number | null
  utilization: number | null
  /** null = 还没有分块数据（没读数 / 这一轮没记字符数）→ 不画堆叠条。 */
  parts: ContextParts | null
}

export interface CacheUsage {
  read_tokens: number | null
  write_tokens: number | null
  hit_ratio: number | null
}

export interface CompactionUsage {
  count: number
  last_compaction_tokens: number | null
  last_step: string | null
}

export interface UsageReport {
  context: ContextUsage
  cache: CacheUsage
  compaction: CompactionUsage
}

export interface Branch {
  name: string
  /** 链尾条目 id；空分支为 null。 */
  tip_entry_id: string | null
  entry_count: number
  is_default: boolean
  /** 该分支最近一次运行的用量快照（落盘；null = 还没跑过）。 */
  usage: UsageReport | null
}

export interface BranchList {
  session_id: string
  branches: Branch[]
}


/**
 * BYOK 模型配置（阶段 34；GET/PUT/POST test/DELETE `/api/settings/byok`）。
 *
 * 三层模型：Provider（接入端点）1—N Model（具体模型 + 能力声明），Binding 把
 * Model 挂到角色槽位（当前只有 chat 一槽）。**密钥只入不出**：PUT 载荷里的
 * `api_key` 有去无回，GET 只给每家的 `key_set` 布尔；鉴权隐式——密钥库里按
 * provider id 存了密钥就按协议标准头发送，没存就不带（本地服务）。
 */
export type ByokProtocol = 'openai-compatible' | 'anthropic' | 'responses' | 'ollama'

/** 能力声明；null = 未声明。只有显式 false 才会被运行期拦截。 */
export interface CapabilityFlags {
  tool_calling?: boolean | null
  vision?: boolean | null
  json_mode?: boolean | null
  streaming?: boolean | null
  reasoning?: boolean | null
}



/** Provider 下的一个具体模型：id + 可选展示名 / 窗口 / 输出上限 / 推理强度 / 能力声明。 */
export interface ModelEntry {
  id: string
  label?: string | null
  context_window?: number | null
  max_output?: number | null
  /** 这个模型认哪些推理强度档位（运行时从列表里挑一个）；空 = 不提这件事。 */
  reasoning_efforts?: string[]
  capabilities: CapabilityFlags
}

/** GET 回显的一个接入端点：与 ProviderInput 同形但没有 api_key，多 key_set。 */
export interface ProviderEntry {
  id: string
  label: string
  protocol: ByokProtocol
  base_url: string
  headers: Record<string, string>
  extra_body: Record<string, unknown>
  enabled: boolean
  models: ModelEntry[]
  key_set: boolean
}

/** PUT 载荷的一个接入端点：api_key 只入不出（空串 = 清除已存密钥）。 */
export interface ProviderInput {
  id: string
  label: string
  protocol: ByokProtocol
  base_url: string
  headers: Record<string, string>
  extra_body: Record<string, unknown>
  enabled: boolean
  models: ModelEntry[]
  api_key?: string | null
}

/** GET /api/settings/byok 的响应：模型连接的唯一来源。 */
export interface ByokSettings {
  providers: ProviderEntry[]
  bindings: Record<string, string | null>
}

/** PUT 载荷：providers 全量 + chat 绑定；服务端 validate 不过就不落盘。 */
export interface ByokSettingsInput {
  providers: ProviderInput[]
  bindings: Record<string, string | null>
}

/** 连通校验的一步：step = 'chat'（最小对话）| 'tool'（工具冒烟）。 */
export interface VerifyStep {
  step: string
  ok: boolean
  detail: string
}

/** POST /api/settings/byok/test 的响应。 */
export interface ByokTestResult {
  ok: boolean
  steps: VerifyStep[]
}
