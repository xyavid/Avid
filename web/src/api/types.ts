/** TS shapes of the REST contract, one-to-one with the pydantic DTOs in `avid/web/schemas.py`. */

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

/** Sandbox backend probe (`capabilities.sandbox` of `GET /api/meta`). */
export interface SandboxState {
  backend: string
  available: boolean
  network: boolean
  reason: string | null
  /** Kernel Landlock ABI; network enforcement needs ABI ≥ 4 (diagnostic display only here). */
  landlock_abi: number | null
}

/** System folder picker result: `path` null = user cancelled (not an error). */
export interface PickFolderResult {
  path: string | null
}

/** Session ownership on the wire; `id` comes from the session header. */
export interface WorkspaceRef {
  id: string | null
  root: string | null
  name: string | null
}

/**
 * One item of `GET /api/workspaces`.
 *
 * `is_default` is only ever true in single-workspace mode; `name` is nullable in the pydantic DTO
 * (`WorkspaceOut` extends `WorkspaceRef`), so it is nullable here too.
 */
export interface WorkspaceSummary {
  id: string
  root: string
  name: string | null
  created_at: number
  last_used_at: number
  is_default: boolean
}

/** BYOK candidate for per-run switching: `ref` = providerId/modelId, `label` = display name. */
export interface ModelCandidate {
  ref: string
  label: string
  /** Reasoning levels this model declares; the input area's picker lists exactly these. */
  reasoning_efforts?: string[]
}

export interface Capabilities {
  tools: string[]
  skills: Skill[]
  model: string | null
  /** Switchable model candidates (BYOK providerId/modelId refs); empty without BYOK config. */
  models: ModelCandidate[]
  workspace: string
  /** Which folder-picker backend this machine uses (null = none); diagnostics. */
  workspace_picker: string | null
  /** Sandbox backend probe: the UI reports whether the sandbox exists instead of guessing. */
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
  /** Owning workspace; set by the session header, read-only for clients. */
  workspace: WorkspaceRef | null
  message_count: number
  active_run_id: string | null
  truncated_tail: boolean
}

export interface SessionDetail extends SessionSummary {
  branch: string
}

/**
 * Scratch session (`POST /api/sessions/{id}/scratch`): context copied from the source session,
 * read-only, destroyed when the panel closes; `copied_messages` says how many messages were copied.
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

/** Workspace file browser (`GET /api/workspaces/{id}/files`) item. */
export interface FileEntry {
  name: string
  /** Relative to the workspace root (POSIX separators). */
  path: string
  kind: 'dir' | 'file'
  /** Directories have no size (that needs recursion). */
  size: number | null
}

export interface FileList {
  path: string
  /** Parent relative path; null at the root. */
  parent: string | null
  entries: FileEntry[]
  truncated: boolean
}

/** File preview: a binary file has no `text` (report the fact, don't guess an encoding). */
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
  /** 'approval' awaits a decision, 'question' awaits an answer — one table, one UI slot. */
  kind: string
  /** Choice options (empty = free-form answer). */
  options: string[]
  decision: string | null
  answer: string | null
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
  /** Latest usage snapshot; null = no reading yet. */
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
 * Unified usage snapshot, same shape as `RunState.usage_report()` and the server's `UsageOut`.
 * A null field means "no such number" (endpoint reported no usage / unknown window / no cache
 * writes) and renders as "—", never as 0: "not reported" differs from "actually zero".
 */
/** Estimated tokens per block (share of the real total; blocks sum to `tokens`). */
export interface ContextParts {
  system: number
  tools: number
  messages: number
}

export interface ContextUsage {
  tokens: number | null
  window: number | null
  utilization: number | null
  /** null = no per-block data yet → do not draw the stacked bar. */
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
  /** Tip entry id; null for an empty branch. */
  tip_entry_id: string | null
  entry_count: number
  is_default: boolean
  /** Latest persisted usage snapshot for this branch; null = never ran. */
  usage: UsageReport | null
}

export interface BranchList {
  session_id: string
  branches: Branch[]
}


/**
 * BYOK model settings (GET/PUT/POST test/DELETE `/api/settings/byok`): providers 1—N models,
 * with bindings mapping a model to a role slot (currently only chat). Keys are write-only: a PUT
 * payload's `api_key` never comes back, GET only reports each provider's `key_set`; auth is
 * implicit — a stored key is sent in the protocol's standard header.
 */
export type ByokProtocol = 'openai-compatible' | 'anthropic' | 'responses' | 'ollama'

/** Capability flags; null = undeclared. Only an explicit false is blocked at runtime. */
export interface CapabilityFlags {
  tool_calling?: boolean | null
  vision?: boolean | null
  json_mode?: boolean | null
  streaming?: boolean | null
  reasoning?: boolean | null
}



/** One model under a provider: id plus optional label / window / output cap / levels / flags. */
export interface ModelEntry {
  id: string
  label?: string | null
  context_window?: number | null
  max_output?: number | null
  /** Levels this model accepts (the runtime picks one from the list); empty = not declared. */
  reasoning_efforts?: string[]
  capabilities: CapabilityFlags
}

/** One endpoint as returned by GET: ProviderInput's shape minus `api_key`, plus `key_set`. */
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

/** One endpoint in a PUT payload: `api_key` is write-only (empty string clears the stored key). */
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

/** `GET /api/settings/byok` response: the single source of model connections. */
export interface ByokSettings {
  providers: ProviderEntry[]
  bindings: Record<string, string | null>
}

/** PUT payload: full providers + chat binding; a failed server-side validation writes nothing. */
export interface ByokSettingsInput {
  providers: ProviderInput[]
  bindings: Record<string, string | null>
}

/** One connectivity-check step: step = 'chat' (minimal chat) | 'tool' (tool smoke). */
export interface VerifyStep {
  step: string
  ok: boolean
  detail: string
}

/** `POST /api/settings/byok/test` response. */
export interface ByokTestResult {
  ok: boolean
  steps: VerifyStep[]
}

/**
 * `GET /api/settings/sessions` response: current/default session dir and where the value comes
 * from. `source` 'env' means `editable` is false — the env var wins over the settings file.
 */
export interface SessionsDir {
  dir: string
  default_dir: string
  source: 'env' | 'settings' | 'default'
  editable: boolean
}

/** PUT payload: empty string restores the default; only changes where new sessions are written. */
export interface SessionsDirInput {
  dir: string
}

/**
 * One content hit: enough to show a snippet and jump back to the source (session + entry + byte
 * offset). `entry_type` is the entry type (message / notice / error), `role` the message role.
 */
export interface SearchHit {
  session_id: string
  entry_id: string
  seq: number
  entry_type: string
  role: string | null
  timestamp: number | null
  snippet: string
  title: string | null
  workspace_id: string | null
  workspace_name: string | null
  byte_offset: number
  byte_length: number
}

/** `GET /api/search` response; behind = sessions the index still lags (0 = everything searched). */
export interface SearchResult {
  hits: SearchHit[]
  behind: number
}

/** A pending input: accepted, not yet in the model context. */
export interface PendingInput {
  input_id: string
  /** now = next step of the active run; after = next turn. */
  mode: string
  text: string
  images: number
  client_id: string | null
  created_at: number
  /** Downgraded from now because its run ended before claiming it. */
  missed: boolean
}

/** Submit result: kind=run means a run started (attach to its stream), input = stayed queued. */
export interface InputAccepted {
  kind: string
  input_id: string | null
  run_id: string | null
  mode: string
}
