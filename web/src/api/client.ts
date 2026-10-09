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

/**
 * 开一个临时会话：从源会话拷一份上下文（投影消息），带只读标记。
 * 只在右列的「临时对话」面板里用——离开面板要把它删掉，别让它在会话列表里留痕。
 */
export function createScratchSession(sourceId: string): Promise<ScratchSession> {
  return request(`/api/sessions/${encodeURIComponent(sourceId)}/scratch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
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
 * （会话现在集中放在 `<AVID_HOME>/sessions/<工作区 id>/`，见设置页「会话存储」。）
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

/** 列一层工作区目录（只读；越界与凭据类由后端拒绝）。 */
export function listFiles(workspaceId: string, path = ''): Promise<FileList> {
  const params = new URLSearchParams({ path })
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/files?${params.toString()}`)
}

/** 读一个文件的预览（超长截断、二进制只报事实）。 */
export function readFile(workspaceId: string, path: string): Promise<FileContent> {
  const params = new URLSearchParams({ path })
  return request(`/api/workspaces/${encodeURIComponent(workspaceId)}/file?${params.toString()}`)
}

/** 弹宿主机文件夹选择器（服务端 AVID_PICKER_CMD）；null = 用户取消，不是错误。 */
export function pickFolder(): Promise<{ path: string | null }> {
  return request('/api/workspaces/pick', { method: 'POST' })
}

export type CreateWorkspaceInput = { path: string; name?: string }

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

/** 一条待发送的图片：原始 base64（不带 data: 前缀）与可选文件名。 */
export type ImageUpload = {
  name?: string | null
  data: string
}

export type InputRequest = {
  /** now = 最早可能被处理的时刻（忙时插进当前 run 的下一个 step，空闲就直接起 run）。 */
  mode: 'now' | 'after'
  prompt: string
  images?: ImageUpload[]
  /** 幂等键：前端因超时重发同一条时不该产生第二条。 */
  client_id?: string
  model?: string
  reasoning_effort?: string
  branch?: string
  full_access_ack?: boolean
}

export type StartRunInput = {
  prompt?: string
  /** 随这条消息发的图片；文本在前、图片按数组顺序在后（阶段 59）。 */
  images?: ImageUpload[]
  /** 这次运行接在哪条链尾上；缺省 = main。 */  branch?: string
  /** 本次运行的模型覆盖；缺省 = 按设置解析（.env + 界面覆盖层）。 */
  model?: string
  /** 本次运行的推理强度：必须在所选模型声明的档位列表里（内核按列表校验）。 */
  reasoning_effort?: string
  /** `true` = 完全访问（跳过毁灭级确认、关沙箱）；唯一的授权凭据，没有模式字段。 */
  full_access_ack?: boolean
  /** 领取一条排队输入去起 run：内容与开关取自那条输入，prompt/images 被忽略（阶段 60）。 */
  from_input?: string
}

/** 一张落库图片的字节地址：读侧端点按 (会话, 条目, 块下标) 定位。 */
export function attachmentUrl(sessionId: string, entryId: string, index: number): string {
  return `/api/sessions/${encodeURIComponent(sessionId)}/entries/${encodeURIComponent(entryId)}/attachments/${index}`
}

/** 发起一次运行（201 → RunCreated）。 */
export function startRun(sessionId: string, input: StartRunInput): Promise<RunCreated> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** 投一条补充输入（阶段 60）：响应告诉它落在哪一类——起了 run，还是留在队里。 */
export function submitInput(sessionId: string, input: InputRequest): Promise<InputAccepted> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/inputs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
}

/** 这个会话还没被采纳的输入（刷新后据此把「排队中」的段落画回来）。 */
export function listInputs(sessionId: string): Promise<PendingInput[]> {
  return request<{ inputs: PendingInput[] }>(
    `/api/sessions/${encodeURIComponent(sessionId)}/inputs`,
  ).then((body) => body.inputs)
}

/** 撤销一条尚未领取的输入（204）。 */
export function dropInput(sessionId: string, inputId: string): Promise<void> {
  return request(
    `/api/sessions/${encodeURIComponent(sessionId)}/inputs/${encodeURIComponent(inputId)}`,
    { method: 'DELETE' },
  )
}

export function getRun(runId: string): Promise<Run> {
  return request(`/api/runs/${encodeURIComponent(runId)}`)
}

/** 请求取消（202；取消是协作式的，实际终态以 run_cancelled 事件 / getRun 为准）。 */
export function cancelRun(runId: string): Promise<CancelResult> {
  return request(`/api/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
}

/** 幂等答复一次提问；answer 是文本（选择题也走同一条）。 */
export function answerApproval(runId: string, approvalId: string, answer: string): Promise<unknown> {
  return request(`/api/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ answer }),
  })
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

/**
 * 会话目录（阶段 56）：改的是「新会话写哪」，不搬已有会话——搬数据是
 * `avid session migrate` 的事。保存后服务端解绑缓存仓库，下一条消息起生效。
 */

/** 读会话目录与它的来源（来源是环境变量时界面只读）。 */
export function getSessionsDir(): Promise<SessionsDir> {
  return request('/api/settings/sessions')
}

/** 保存会话目录；空串恢复默认。目录由服务端就地建好，建不出就是 400。 */
export function setSessionsDir(dir: string): Promise<SessionsDir> {
  return request('/api/settings/sessions', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dir }),
  })
}

/**
 * 内容检索（阶段 57）：走本地索引（SQLite + FTS5），不调模型。
 * 索引只服务检索——列表仍以 JSONL 为准，所以这个接口可能落后（响应里的 behind）。
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
