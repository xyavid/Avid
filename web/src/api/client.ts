/**
 * 网络出口唯一层：所有请求经这里，组件不直接 fetch。
 *
 * 错误归一（老前端同判据，阶段 33 报告过）：
 * - 非 2xx 且响应是约定的 JSON 信封（{error:{code,message,detail}}）→ 按信封抛；
 * - 非 2xx 但不是约定 JSON → 额外探一次 /api/health，区分「后端不在这儿」
 *   （代理伪造的 5xx）与「后端答坏了」——两条给用户的话完全不同；
 * - fetch 直接抛错（后端没起）→ 给可执行的下一步。
 */

import type { BranchList, EntryPage, Meta, SessionSummary, WorkspaceSummary } from './types'

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

/** 同源请求；开发期由 vite 代理 /api → 8765。 */
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
    // 非 JSON：走下面的 health 探测分流
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
      // 后端活着却回了一份非约定格式 → 是后端答坏了，不是连不上。
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

export type ListEntriesOptions = { branch?: string; limit?: number; cursorSeq?: number }

export function listEntries(sessionId: string, opts: ListEntriesOptions = {}): Promise<EntryPage> {
  const params = new URLSearchParams({ branch: opts.branch ?? 'main', order: 'desc' })
  if (opts.limit !== undefined) params.set('limit', String(opts.limit))
  if (opts.cursorSeq !== undefined) params.set('cursor_seq', String(opts.cursorSeq))
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/entries?${params.toString()}`)
}

/** 分支清单（含每分支落盘的用量快照——上下文卡的读数来源）。 */
export function listBranches(sessionId: string): Promise<BranchList> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/branches`)
}

export function listWorkspaces(): Promise<{ workspaces: WorkspaceSummary[] }> {
  return request('/api/workspaces')
}

/** 弹宿主机文件夹选择器（服务端 AVID_PICKER_CMD）；null = 用户取消，不是错误。 */
export function pickFolder(): Promise<{ path: string | null }> {
  return request('/api/workspaces/pick', { method: 'POST' })
}

export type CreateWorkspaceInput = { path: string; name?: string; permission?: 'manual' | 'auto' }

/** 注册工作区；已注册时后端 409 workspace_exists（detail 带既有 id/name/root）。 */
export function createWorkspace(input: CreateWorkspaceInput): Promise<WorkspaceSummary> {
  return request('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}
