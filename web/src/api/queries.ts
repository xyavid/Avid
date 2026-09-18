/**
 * TanStack Query hooks：**只放 REST**。
 *
 * 权威域（会话列表、条目历史、任务图、技能目录、运行状态）走这里；
 * 事件绝不写进查询缓存（设计文档 §3.5）——活动运行那部分在 `state/runStore`。
 * 取数位置纪律：需要数据的组件自己取数（在 L2 的 hook 里），不在 route 顶层取
 * 再透传。
 */

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { request } from './client'
import type {
  Approval,
  ApprovalAnswer,
  Branch,
  BranchList,
  CancelResult,
  EntryPage,
  Meta,
  Run,
  RunCreated,
  SessionDetail,
  SessionSummary,
  Skill,
  StartRunInput,
  Task,
  WorkspaceSummary,
} from './types'

export const ENTRY_PAGE_SIZE = 100

export const queryKeys = {
  meta: ['meta'] as const,
  skills: ['skills'] as const,
  workspaces: ['workspaces'] as const,
  sessions: ['sessions'] as const,
  session: (id: string) => ['session', id] as const,
  entries: (id: string, branch: string) => ['entries', id, branch] as const,
  branches: (id: string) => ['branches', id] as const,
  run: (id: string) => ['run', id] as const,
  approvals: (id: string) => ['approvals', id] as const,
  tasks: ['tasks'] as const,
  task: (id: string) => ['task', id] as const,
}

export function useMeta() {
  return useQuery({
    queryKey: queryKeys.meta,
    queryFn: () => request<Meta>('/meta'),
    staleTime: 60_000,
  })
}

export function useSkills() {
  return useQuery({
    queryKey: queryKeys.skills,
    queryFn: () => request<{ skills: Skill[] }>('/skills').then((page) => page.skills),
    staleTime: 60_000,
  })
}

/**
 * 已知工作区候选（阶段 18）。排序由服务端给：单工作区模式下默认那个排在最前，
 * 其余按最近使用。
 */
export function useWorkspaces() {
  return useQuery({
    queryKey: queryKeys.workspaces,
    queryFn: () =>
      request<{ workspaces: WorkspaceSummary[] }>('/workspaces').then(
        (page) => page.workspaces,
      ),
    staleTime: 5_000,
  })
}

export function useSessionList() {
  return useQuery({
    queryKey: queryKeys.sessions,
    queryFn: () =>
      request<{ sessions: SessionSummary[] }>('/sessions').then((page) => page.sessions),
    staleTime: 5_000,
  })
}

export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: queryKeys.session(sessionId ?? ''),
    queryFn: () => request<SessionDetail>(`/sessions/${sessionId}`),
    enabled: Boolean(sessionId),
  })
}

/** 条目分页：游标是 `cursor_seq`，服务端默认 100 / 硬上限 500（I14）。 */
export function useEntries(sessionId: string | null, branch = 'main') {
  return useInfiniteQuery({
    queryKey: queryKeys.entries(sessionId ?? '', branch),
    enabled: Boolean(sessionId),
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ branch, order: 'desc', limit: String(ENTRY_PAGE_SIZE) })
      if (pageParam !== null) params.set('cursor_seq', String(pageParam))
      return request<EntryPage>(`/sessions/${sessionId}/entries?${params.toString()}`)
    },
    getNextPageParam: (last) => (last.has_more ? last.next_cursor : undefined),
  })
}

/** 分支列表：切换与分叉都以它为准（服务端把 main 作为隐式默认返回）。 */
export function useBranches(sessionId: string | null) {
  return useQuery({
    queryKey: queryKeys.branches(sessionId ?? ''),
    queryFn: () => request<BranchList>(`/sessions/${sessionId}/branches`),
    enabled: Boolean(sessionId),
    staleTime: 5_000,
  })
}

export function useRunStatus(runId: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.run(runId ?? ''),
    queryFn: () => request<Run>(`/runs/${runId}`),
    enabled: Boolean(runId) && enabled,
  })
}

export function usePendingApprovals(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.approvals(runId ?? ''),
    queryFn: () =>
      request<{ approvals: Approval[] }>(`/runs/${runId}/approvals`).then(
        (page) => page.approvals,
      ),
    enabled: Boolean(runId),
  })
}

export function useTaskList() {
  return useQuery({
    queryKey: queryKeys.tasks,
    queryFn: () => request<{ tasks: Task[] }>('/tasks').then((page) => page.tasks),
    staleTime: 5_000,
  })
}

export function useTask(taskId: string | null) {
  return useQuery({
    queryKey: queryKeys.task(taskId ?? ''),
    queryFn: () => request<Task>(`/tasks/${taskId}`),
    enabled: Boolean(taskId),
  })
}

// ---------------- 写操作 ----------------

export function useCreateSession() {
  const client = useQueryClient()
  return useMutation({
    // 多工作区模式下 `workspace` 必填（服务端缺它会 400 workspace_required）；
    // 单工作区模式省略也行，但 UI 总是把选中的那个显式发出去，归属不靠默认值猜。
    mutationFn: (input: { name?: string | null; workspace?: string }) =>
      request<SessionDetail>('/sessions', { method: 'POST', body: input }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions })
    },
  })
}

export function useRenameSession() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { id: string; name: string }) =>
      request<SessionDetail>(`/sessions/${input.id}`, {
        method: 'PATCH',
        body: { name: input.name },
      }),
    onSuccess: (detail) => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions })
      void client.invalidateQueries({ queryKey: queryKeys.session(detail.id) })
    },
  })
}

export function useDeleteSession() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => request<void>(`/sessions/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sessions })
    },
  })
}

/** 在某条目处开新分支（fork）。名字缺省时由服务端取 b2 / b3… */
export function useCreateBranch() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { sessionId: string; name?: string | null; at?: string | null }) =>
      request<Branch>(`/sessions/${input.sessionId}/branches`, {
        method: 'POST',
        body: { name: input.name ?? null, at: input.at ?? null },
      }),
    onSuccess: (branch, input) => {
      void client.invalidateQueries({ queryKey: queryKeys.branches(input.sessionId) })
      void client.invalidateQueries({
        queryKey: queryKeys.entries(input.sessionId, branch.name),
      })
    },
  })
}

export function useStartRun() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { sessionId: string; run: StartRunInput }) =>
      request<RunCreated>(`/sessions/${input.sessionId}/runs`, {
        method: 'POST',
        body: input.run,
      }),
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: queryKeys.session(created.session_id) })
      void client.invalidateQueries({ queryKey: queryKeys.sessions })
    },
  })
}

export function useCancelRun() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) =>
      request<CancelResult>(`/runs/${runId}/cancel`, { method: 'POST' }),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: queryKeys.run(result.run_id) })
    },
  })
}

export function useAnswerApproval() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (input: { runId: string; approvalId: string; decision: 'allow' | 'deny' }) =>
      request<ApprovalAnswer>(`/runs/${input.runId}/approvals/${input.approvalId}`, {
        method: 'POST',
        body: { decision: input.decision },
      }),
    onSuccess: (_answer, input) => {
      void client.invalidateQueries({ queryKey: queryKeys.approvals(input.runId) })
      void client.invalidateQueries({ queryKey: queryKeys.run(input.runId) })
    },
  })
}
