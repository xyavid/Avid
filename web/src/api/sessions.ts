/**
 * 端点封装：把 `docs/guide/web-ui.md` §2 的表格逐行变成函数。
 *
 * 这一层只做三件事，别的都不做：
 *   · 拼路径与查询串（`branch` / `order` / `limit` / `cursor_seq`）；
 *   · 拆掉列表响应的外壳（`{sessions: [...]}` → `[...]`），让调用方拿到数组；
 *   · 把 `AbortSignal` 一路透传给 `request`（切会话 / 卸载组件时要能真的取消）。
 *
 * **不在这里做缓存、不去重、不重试**：重试语义在 SSE（`stream.ts`）与上层状态层，
 * 缓存语义也不属于"网络出口"。每个写操作都允许传 `signal`。
 */

import { request } from './client'
import type {
  Approval,
  ApprovalAnswer,
  BranchList,
  CancelResult,
  EntryPage,
  Meta,
  Run,
  RunCreated,
  SessionDetail,
  SessionSummary,
  StartRunInput,
  WorkspaceSummary,
} from './types'

/**
 * 列表响应的外壳。服务端 `schemas.py` 的 `SessionListOut` / `WorkspaceListOut` /
 * `ApprovalListOut` 都是"一条 `list` 字段"的形状，而 `api/types.ts`（契约种子）
 * 只声明前端真正读的实体类型，没有这三个外壳——所以它们定义在这里。
 */
export interface SessionListOut {
  sessions: SessionSummary[]
}

export interface WorkspaceListOut {
  workspaces: WorkspaceSummary[]
}

/**
 * 待决审批列表。
 *
 * 服务端 `ApprovalListOut` 只有 `approvals` 一个字段（`schemas.py:301`）——
 * 没有 `run_id`：run 是路径参数，响应里再带一遍是冗余的。
 */
export interface ApprovalListOut {
  approvals: Approval[]
}

/** 只把有值的参数写进查询串：`?limit=undefined` 会被服务端当成非法值。 */
function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

// ---------------- 元信息 ----------------

export function fetchMeta(signal?: AbortSignal): Promise<Meta> {
  return request<Meta>('/meta', { signal })
}

// ---------------- 工作区 ----------------

/** 已登记的工作区候选列表；服务端排序（单工作区模式的默认那个在最前）。 */
export function fetchWorkspaces(signal?: AbortSignal): Promise<WorkspaceSummary[]> {
  return request<WorkspaceListOut>('/workspaces', { signal }).then((page) => page.workspaces)
}

// ---------------- 会话 ----------------

export function fetchSessions(signal?: AbortSignal): Promise<SessionSummary[]> {
  return request<SessionListOut>('/sessions', { signal }).then((page) => page.sessions)
}

export function fetchSession(id: string, signal?: AbortSignal): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, { signal })
}

/**
 * 新建会话。
 *
 * `workspace` 是必填的（缺了服务端一律 400 `workspace_required`）：会话归属是创建时的
 * 不可变事实，不该由"进程当前在哪"决定，所以界面上必须选一次。
 */
export function createSession(
  input: { workspace: string; name?: string },
  signal?: AbortSignal,
): Promise<SessionDetail> {
  return request<SessionDetail>('/sessions', {
    method: 'POST',
    body: { workspace: input.workspace, name: input.name },
    signal,
  })
}

export function renameSession(
  id: string,
  name: string,
  signal?: AbortSignal,
): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: { name },
    signal,
  })
}

/** 销毁会话（204，无正文）。服务端在会话有活动 run 时返回 409，由调用方提示。 */
export function deleteSession(id: string, signal?: AbortSignal): Promise<void> {
  return request<void>(`/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', signal })
}

export function fetchEntries(
  id: string,
  params: { branch?: string; order?: 'asc' | 'desc'; limit?: number; cursor_seq?: number },
  signal?: AbortSignal,
): Promise<EntryPage> {
  return request<EntryPage>(
    `/sessions/${encodeURIComponent(id)}/entries${query({
      branch: params.branch,
      order: params.order,
      limit: params.limit,
      cursor_seq: params.cursor_seq,
    })}`,
    { signal },
  )
}

export function fetchBranches(id: string, signal?: AbortSignal): Promise<BranchList> {
  return request<BranchList>(`/sessions/${encodeURIComponent(id)}/branches`, { signal })
}

// ---------------- 运行 ----------------

export function startRun(
  sessionId: string,
  input: StartRunInput,
  signal?: AbortSignal,
): Promise<RunCreated> {
  return request<RunCreated>(`/sessions/${encodeURIComponent(sessionId)}/runs`, {
    method: 'POST',
    body: input,
    signal,
  })
}

export function fetchRun(runId: string, signal?: AbortSignal): Promise<Run> {
  return request<Run>(`/runs/${encodeURIComponent(runId)}`, { signal })
}

/** 请求取消：服务端在下一个检查点生效，返回的 `status` 仍是 `running`。 */
export function cancelRun(runId: string, signal?: AbortSignal): Promise<CancelResult> {
  return request<CancelResult>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    signal,
  })
}

/** 当前待决审批：刷新与第二个标签页靠它恢复（SSE 重放也会重发 `approval_requested`）。 */
export function fetchApprovals(runId: string, signal?: AbortSignal): Promise<ApprovalListOut> {
  return request<ApprovalListOut>(`/runs/${encodeURIComponent(runId)}/approvals`, { signal })
}

/**
 * 答复一条审批。
 *
 * 重复投递返回 200 且 `accepted: false`、`already` 非空（服务端不二次批准），
 * 所以调用方要读 `accepted` 而不是把 2xx 当成"已生效"。
 */
export function answerApproval(
  runId: string,
  approvalId: string,
  decision: 'allow' | 'deny',
  signal?: AbortSignal,
): Promise<ApprovalAnswer> {
  return request<ApprovalAnswer>(
    `/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalId)}`,
    { method: 'POST', body: { decision }, signal },
  )
}
