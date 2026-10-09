/**
 * The single network exit: all requests go through here, components never call fetch directly.
 * A non-2xx JSON envelope `{error:{code,message,detail}}` is rethrown as ApiError, a non-JSON
 * failure probes `/api/health` to tell "backend not running" from "backend answered badly",
 * and a fetch throw means the service is down.
 */

import type {
  Branch,
  BranchList,
  ByokSettings,
  ByokSettingsInput,
  ByokTestResult,
  CancelResult,
  EntryPage,
  FileContent,
  FileList,
  InputAccepted,
  Meta,
  PendingInput,
  Run,
  RunCreated,
  ScratchSession,
  SearchResult,
  SessionDetail,
  SessionSummary,
  SessionsDir,
  WorkspaceSummary,
} from './types'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly detail: Record<string, unknown> | null

  constructor(code: string, message: string, status: number, detail: Record<string, unknown> | null) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.detail = detail
  }
}

/** Same-origin fetch; in dev, vite proxies /api to port 8765. */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError('network', '连不上本地服务：请确认 `avid web` 已启动、端口与访问地址一致。', 0, null)
  }
  if (res.ok) {
    return (res.status === 204 ? undefined : await res.json()) as T
  }

  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // Not JSON: fall through to the health probe below.
  }
  const envelope = body as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } } | null
  if (envelope?.error?.message) {
    throw new ApiError(
      envelope.error.code ?? 'unknown',
      envelope.error.message,
      res.status,
      envelope.error.detail ?? null,
    )
  }

  try {
    if ((await fetch('/api/health')).ok) {
      // Backend alive but answering outside the envelope: it broke, not a connection failure.
      throw new ApiError('bad_gateway', `后端返回了无法解析的响应（HTTP ${res.status}），详情见服务端日志。`, res.status, null)
    }
  } catch (err) {
    if (err instanceof ApiError) throw err
  }
  throw new ApiError('network', '连不上本地服务：请确认 `avid web` 已启动、端口与访问地址一致。', 0, null)
}

export function getMeta(): Promise<Meta> {
  return request('/api/meta')
}

export function listSessions(): Promise<{ sessions: SessionSummary[] }> {
  return request('/api/sessions')
}

export type CreateSessionInput = { workspace: string; name?: string | null }

/**
 * Create a session (201 → SessionDetail). `workspace` is required server-side: ownership is
 * fixed once written, and there is no "default workspace"; the server generates id and files.
 */
export function createSession(input: CreateSessionInput): Promise<SessionDetail> {
  return request('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/**
 * Scratch session: a copy of the source session's context (projected messages), marked read-only.
 * Used only by the right dock's scratch panel — destroy it when leaving the panel.
 */
export function createScratchSession(sourceId: string): Promise<ScratchSession> {
  return request(`/api/sessions/${encodeURIComponent(sourceId)}/scratch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
}

/** Rename (PATCH carries name only). The server accepts an empty name; the UI never submits one. */
export function renameSession(sessionId: string, name: string): Promise<SessionDetail> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

/**
 * Delete a session (204): this destroys the session files on disk and cannot be undone —
 * unlike removing a workspace, which only drops the registry entry. 409 session_busy while a run
 * is active. Sessions live under `<AVID_HOME>/sessions/<workspace id>/` (see the settings page).
 */
export function deleteSession(sessionId: string): Promise<void> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' })
}

export type ListEntriesOptions = { branch?: string; limit?: number; cursorSeq?: number }

export function listEntries(sessionId: string, opts: ListEntriesOptions = {}): Promise<EntryPage> {
  const params = new URLSearchParams({ branch: opts.branch ?? 'main', order: 'desc' })
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.cursorSeq !== undefined) params.set('cursor_seq', String(opts.cursorSeq))
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/entries?${params.toString()}`)
}

/** Branch list; each branch carries its persisted usage snapshot (the context card's reading). */
export function listBranches(sessionId: string): Promise<BranchList> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/branches`)
}

/**
 * Fork a branch at an entry (the server names it when `name` is missing; no `at` starts an empty
 * branch). 409 while the session has an active run: the fork point must not move mid-append.
 */
export function createBranch(
  sessionId: string,
  input: { name?: string; at?: string } = {},
): Promise<Branch> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/branches`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function listWorkspaces(): Promise<{ workspaces: WorkspaceSummary[] }> {
  return request('/api/workspaces')
}

/** List one directory level (read-only); the server refuses out-of-root and credential paths. */
export function listFiles(workspaceId: string, path = ''): Promise<FileList> {
  const params = new URLSearchParams({ path })
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files?${params.toString()}`)
}

/** File preview (long text truncated, binaries only report the fact). */
export function readFile(workspaceId: string, path: string): Promise<FileContent> {
  const params = new URLSearchParams({ path })
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/file?${params.toString()}`)
}

/** Host folder picker (server-side AVID_PICKER_CMD); null = user cancelled, not an error. */
export function pickFolder(): Promise<{ path: string | null }> {
  return request('/api/workspaces/pick', { method: 'POST' })
}

export type CreateWorkspaceInput = { path: string; name?: string }

/** Register a workspace; 409 workspace_exists when already registered (detail has id/name/root). */
export function createWorkspace(input: CreateWorkspaceInput): Promise<WorkspaceSummary> {
  return request('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/**
 * Remove a workspace from the project list (registry entry, 204).
 * The session files on disk stay: re-registering the same directory brings them back.
 */
export function deleteWorkspace(id: string): Promise<void> {
  return request(`/api/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** An image to send: raw base64 (no data: prefix) and an optional name. */
export type ImageUpload = {
  name?: string | null
  data: string
}

export type InputRequest = {
  /** now = next step of the active run, or a fresh run when idle; after = next turn. */
  mode: 'now' | 'after'
  prompt: string
  images?: ImageUpload[]
  /** Idempotency key: a client retry must not create a duplicate input. */
  client_id?: string
  model?: string
  reasoning_effort?: string
  branch?: string
  full_access_ack?: boolean
}

export type StartRunInput = {
  prompt?: string
  /** Images sent with this message: text blocks first, images in array order after. */
  images?: ImageUpload[]
  /** Which branch tip this run attaches to; omitted = main. */
  branch?: string
  /** Model override for this run; omitted = resolved from settings. */
  model?: string
  /** Reasoning level for this run: must be one of the chosen model's declared levels. */
  reasoning_effort?: string
  /** `true` = full access (skip destructive confirmations, sandbox off); the only credential. */
  full_access_ack?: boolean
  /** Claim a queued input to start a run from: prompt/images are ignored, content comes from it. */
  from_input?: string
}

/** Byte URL of a stored image: the read endpoint addresses it by (session, entry, block index). */
export function attachmentUrl(sessionId: string, entryId: string, index: number): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/entries/${encodeURIComponent(entryId)}/attachments/${index}`
}

/** Start a run (201 → RunCreated). */
export function startRun(sessionId: string, input: StartRunInput): Promise<RunCreated> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** Submit a supplemental input; the response says whether it started a run or stayed queued. */
export function submitInput(sessionId: string, input: InputRequest): Promise<InputAccepted> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/inputs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** Inputs of this session not yet taken into model context. */
export function listInputs(sessionId: string): Promise<PendingInput[]> {
  return request<{ inputs: PendingInput[] }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/inputs`,
  ).then((body) => body.inputs)
}

/** Drop an unclaimed input (204). */
export function dropInput(sessionId: string, inputId: string): Promise<void> {
  return request(
    `/api/sessions/${encodeURIComponent(sessionId)}/inputs/${encodeURIComponent(inputId)}`,
    { method: 'DELETE' },
  )
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/runs/${encodeURIComponent(runId)}`)
}

/** Request cancel (202); cooperative — the terminal state arrives via run_cancelled or getRun. */
export function cancelRun(runId: string): Promise<CancelResult> {
  return request(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

/** Answer a question idempotently; the answer is text (choice questions use the same path). */
export function answerApproval(runId: string, approvalId: string, answer: string): Promise<unknown> {
  return request(`/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  })
}

/** Decide an approval idempotently; decision: 'allow' | 'deny'. */
export function decideApproval(runId: string, approvalId: string, decision: 'allow' | 'deny'): Promise<unknown> {
  return request(`/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
}

/** BYOK model settings. Keys are write-only: PUT carries `api_key`, GET only reports `key_set`. */

/** Read the whole BYOK config (providers + chat binding). */
export function getByokSettings(): Promise<ByokSettings> {
  return request('/api/settings/byok')
}

/** Save the full config (providers + chat binding); a failed server-side validation returns 400. */
export function saveByokSettings(input: ByokSettingsInput): Promise<ByokSettings> {
  return request('/api/settings/byok', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/**
 * Two-step connectivity check (minimal chat + tool smoke), run against the payload rather than
 * saved config: testable before saving, and the key goes only in the request body.
 */
export function testByokModel(
  provider: ByokSettingsInput['providers'][number],
  modelId: string,
): Promise<ByokTestResult> {
  return request('/api/settings/byok/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model_id: modelId }),
  })
}

/** Delete the config and key files; runs report "no model configured" until saved again. */
export function resetByokSettings(): Promise<void> {
  return request('/api/settings/byok', { method: 'DELETE' })
}

/**
 * Session dir: only changes where new sessions are written; existing ones move via
 * `avid session migrate`.
 */

/** Read the session dir and its source (the UI is read-only when the source is an env var). */
export function getSessionsDir(): Promise<SessionsDir> {
  return request('/api/settings/sessions')
}

/** Save the session dir; empty restores the default; a dir the server cannot create is 400. */
export function setSessionsDir(dir: string): Promise<SessionsDir> {
  return request('/api/settings/sessions', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir }),
  })
}

/**
 * Content search over the local index (SQLite + FTS5, no model call).
 * JSONL stays authoritative, so this endpoint can lag (see `behind` in the response).
 */
export function searchEntries(
  query: string,
  opts: { workspace?: string | null; session?: string | null; limit?: number } = {},
): Promise<SearchResult> {
  const params = new URLSearchParams({ q: query })
  if (opts.workspace) params.set('workspace', opts.workspace)
  if (opts.session) params.set('session', opts.session)
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  return request(`/api/search?${params.toString()}`)
}
