/**
 * 网络出口唯一层：所有请求经这里，组件不直接 fetch。
 *
 * 错误归一（老前端同判据，阶段 33 报告过）：
 * - 非 2xx 且响应是约定的 JSON 信封（{error:{code,message,detail}}）→ 按信封抛；
 * - 非 2xx 但不是约定 JSON → 额外探一次 /api/health，区分「后端不在这儿」
 *   （代理伪造的 5xx）与「后端答坏了」——两条给用户的话完全不同；
 * - fetch 直接抛错（后端没起）→ 给可执行的下一步。
 */

import type {
  Branch,
  BranchList,
  ByokSettings,
  ByokSettingsInput,
  ByokTestResult,
  CancelResult,
  EntryPage,
  Meta,
  Run,
  RunCreated,
  SessionDetail,
  SessionSummary,
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

export type CreateSessionInput = { workspace: string; name?: string | null }

/**
 * 新建会话（201 → SessionDetail）。`workspace` 是服务端的必填项——归属是一经写入
 * 不可变的既成事实，没有「默认工作区」这回事；服务端负责生成 id 与磁盘文件。
 */
export function createSession(input: CreateSessionInput): Promise<SessionDetail> {
  return request('/api/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** 重命名（PATCH 只带 name）。空名字服务端不拒，但 UI 在本地就不提交。 */
export function renameSession(sessionId: string, name: string): Promise<SessionDetail> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

/**
 * 删除会话（204 无正文）。这会**销毁磁盘上的会话记录文件**——与「从项目列表移除、
 * 会话文件还在」的工作区删除不同，删完不可恢复；有活动 run 时服务端回 409 session_busy。
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

/** 分支清单（含每分支落盘的用量快照——上下文卡的读数来源）。 */
export function listBranches(sessionId: string): Promise<BranchList> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/branches`)
}

/**
 * 从某条目分叉出一条分支（缺 name 由服务端起名，缺 at 起一条空分支）。
 * 会话有活动 run 时后端 409——分叉点不能在别人还在往链尾追加时被切走。
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

/**
 * 从项目列表移除工作区（注册表条目，204 无正文）。
 * 它不会删磁盘上的会话文件——那些会话仍留在原目录的 `.avid/sessions/` 下，
 * 重新登记同一个目录就会再出现。UI 上也照实这么说。
 */
export function deleteWorkspace(id: string): Promise<void> {
  return request(`/api/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export type StartRunInput = {
  prompt: string
  /** 这次运行接在哪条链尾上；缺省 = main。 */  branch?: string
  /** 权限模式；缺省由服务端按会话所属工作区的默认权限回落。 */
  permission?: 'manual' | 'auto' | 'full'
  /** 本次运行的模型覆盖；缺省 = 按设置解析（.env + 界面覆盖层）。 */
  model?: string
  /** permission: 'full' 的显式授权凭据——少了它服务端 422（full 三重锁）。 */
  full_access_ack?: boolean
}

/** 发起一次运行（201 → RunCreated）。 */
export function startRun(sessionId: string, input: StartRunInput): Promise<RunCreated> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/runs/${encodeURIComponent(runId)}`)
}

/** 请求取消（202；取消是协作式的，实际终态以 run_cancelled 事件 / getRun 为准）。 */
export function cancelRun(runId: string): Promise<CancelResult> {
  return request(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

/** 幂等答复一次审批；decision: 'allow' | 'deny'。 */
export function decideApproval(runId: string, approvalId: string, decision: 'allow' | 'deny'): Promise<unknown> {
  return request(`/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
}

/**
 * BYOK 模型配置（阶段 34）。密钥只入不出：PUT 载荷的 api_key 有去无回，
 * GET 只给每家的 key_set。保存后对下一条消息立即生效，无需重启。
 */

/** 读整份 BYOK 配置（providers + chat 绑定）。 */
export function getByokSettings(): Promise<ByokSettings> {
  return request('/api/settings/byok')
}

/** 整体保存（providers 全量 + chat 绑定）；validate 不过服务端 400 不落盘。 */
export function saveByokSettings(input: ByokSettingsInput): Promise<ByokSettings> {
  return request('/api/settings/byok', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/**
 * 两步连通校验（最小对话 + 工具冒烟），针对**载荷**而不是已保存配置——
 * 保存前就能测；密钥走载荷，不读也不写密钥文件。
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

/** 重置：删配置与密钥两份文件；之后运行会报「还没有模型配置」，直到重新保存。 */
export function resetByokSettings(): Promise<void> {
  return request('/api/settings/byok', { method: 'DELETE' })
}
