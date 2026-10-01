/**
 * 会话层的数据读取与变更（唯一调用 `api/sessions.ts` 的地方）。
 *
 * 为什么单独成 hook 而不是塞进 Provider：这里全是副作用（请求、轮询、AbortSignal），
 * 而 Provider 应当是"状态 + 动作"的薄壳。分开之后，Provider 里没有一处 `fetch`，
 * 也就不可能出现"组件卸载后请求回来把状态写回去"那类问题——订阅与清理都关在本文件里。
 *
 * 轮询取舍（**本次不做真后端联调，这是显式选择**）：
 * 会话列表的 `active_run_id` 只有服务端知道，而列表没有对应的事件通道。
 * 这里给它一个 5 秒的轻轮询，且**只在标签页可见且确有活动 run 时**才走网络；
 * 没有活动 run 时不轮询——空转的请求会让"前端到底在干什么"变得难以排查。
 * 真正的事件驱动来自 `useRunStream` 的 SSE，不是这里。
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import {
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  fetchMeta,
  fetchSessions,
  fetchWorkspaces,
  renameSession as apiRenameSession,
} from '../api/sessions'
import type { Meta, SessionSummary, WorkspaceSummary } from '../api/types'

/** 有活动 run 时的列表轮询间隔。不是"实时"手段，是兜底。 */
export const SESSION_POLL_MS = 5_000

export interface SessionData {
  meta: Meta | null
  sessions: SessionSummary[]
  workspaces: WorkspaceSummary[]
  loading: boolean
  error: string | null
  /** 新建会话时可用的工作区根路径；null = 服务端与注册表都没给，不能建会话。 */
  defaultWorkspace: string | null
  refresh: () => Promise<void>
  create: (name?: string) => Promise<SessionSummary>
  rename: (id: string, name: string) => Promise<void>
  remove: (id: string) => Promise<void>
}

/** 把 `ApiError` 的稳定错误码与消息拼成一句可读的话；非 ApiError 不吞掉原因。 */
export function describeError(error: unknown): string {
  /*
   * 先判 `Error` 再判 `ApiError`：`instanceof` 的顺序让类型收窄成立，
   * 也保证了一个不是 Error 的抛出物（后端极少见，但 `Promise.reject('x')` 会有）
   * 仍然能被显示成原文，而不是 "[object Object]"。
   */
  if (error instanceof Error) {
    if (error instanceof ApiError) {
      return error.code === 'network_error' ? '连不上本地服务' : `${error.message}（${error.code}）`
    }
    return error.message
  }
  return String(error)
}

/**
 * 会话数据层。
 *
 * `enabled: false` 时不发一个请求、也不进入 loading 态，直接给一份"空的、已就绪"
 * 数据。这是给测试注入视图用的（见 `AppProvider` 的 `data` 注入点）。
 *
 * 为什么需要这个开关，而不是让调用方"注入了就不调这个钩子"：
 * React 的 Hooks 规则不允许条件调用，所以 `AppProvider` 必须无条件调用它。
 * 而一旦它真的跑起来，注入视图旁边就会多出一份**真实**（在 jsdom 里必然失败）的
 * 数据层——两份状态的竞争会让"注入的会话列表"在某些渲染次序下被清空，
 * 表现是页面莫名回落成空态。用显式的 `enabled` 把这条路堵死：
 * 要么真跑，要么完全不跑，不存在"跑了但结果被丢掉"的中间态。
 */
export function useSessionData(enabled = true): SessionData {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([])
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState<string | null>(null)

  /*
   * 每次请求带自己的 AbortController，并在发起下一轮前 abort 掉上一轮。
   * 理由：快速切换标签页/重复点刷新会让多个请求并发返回，后到的旧响应会覆盖新状态。
   * 这里用"取消旧的"而不是"丢弃旧的"，因为取消还能省掉服务端的活。
   */
  const inflight = useRef<AbortController | null>(null)

  const load = useCallback(async (): Promise<void> => {
    // 关闭时不发请求；`enabled` 变化会重建本身，所以这里不需要额外守卫。
    if (!enabled) return
    inflight.current?.abort()
    const controller = new AbortController()
    inflight.current = controller
    try {
      /*
       * 三个请求并发：`/meta`（能力面）、`/sessions`（列表）、`/workspaces`（分组依据）。
       * 它们之间没有依赖，串行只会让首屏慢三倍。
       */
      const [nextMeta, nextSessions, nextWorkspaces] = await Promise.all([
        fetchMeta(controller.signal),
        fetchSessions(controller.signal),
        fetchWorkspaces(controller.signal),
      ])
      if (controller.signal.aborted) return
      setMeta(nextMeta)
      setSessions(nextSessions)
      setWorkspaces(nextWorkspaces)
      setError(null)
    } catch (caught: unknown) {
      // abort 是主动取消，不是错误——把它报成错误会在切换会话时闪一条假告警。
      if (controller.signal.aborted) return
      setError(describeError(caught))
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled) return undefined
    void load()
    return () => inflight.current?.abort()
  }, [enabled, load])

  /*
   * 兜底轮询：只在"确有活动 run"时才转起来。
   * `hasActiveRun` 作为依赖意味着没有活动 run 时这段 effect 不会挂任何定时器。
   */
  const hasActiveRun = sessions.some((session) => session.active_run_id !== null)
  useEffect(() => {
    if (!hasActiveRun) return undefined
    const timer = setInterval(() => {
      // 后台标签页不轮询：用户看不见，请求只会占着服务端的重放缓冲。
      if (document.visibilityState === 'visible') void load()
    }, SESSION_POLL_MS)
    return () => clearInterval(timer)
  }, [hasActiveRun, load])

  /*
   * 工作区根路径的取法有先后：进程绑定值（`capabilities.workspace`）优先，
   * 它一定是存在的目录；没有就取注册表第一项。都没有则返回 null——
   * 此时界面必须**禁用**新建，而不是发一个注定 400 `workspace_required` 的请求。
   */
  const defaultWorkspace = meta?.capabilities.workspace ?? workspaces[0]?.root ?? null

  const create = useCallback(
    async (name?: string): Promise<SessionSummary> => {
      if (defaultWorkspace === null) {
        throw new ApiError(0, 'workspace_required', '没有可用工作区：先用 avid workspace add 登记一个')
      }
      const created = await apiCreateSession(
        name === undefined || name.trim() === ''
          ? { workspace: defaultWorkspace }
          : { workspace: defaultWorkspace, name },
      )
      setSessions((current) => [created, ...current])
      return created
    },
    [defaultWorkspace],
  )

  const rename = useCallback(async (id: string, name: string): Promise<void> => {
    const updated = await apiRenameSession(id, name)
    setSessions((current) => current.map((item) => (item.id === id ? updated : item)))
  }, [])

  const remove = useCallback(async (id: string): Promise<void> => {
    await apiDeleteSession(id)
    setSessions((current) => current.filter((item) => item.id !== id))
  }, [])

  return {
    meta,
    sessions,
    workspaces,
    loading,
    error,
    defaultWorkspace,
    refresh: load,
    create,
    rename,
    remove,
  }
}

/**
 * 404 的判定单独抽出来给上层用：会话被别的标签页删掉时，`/sessions/{id}/entries`
 * 会 404，此时正确的动作是**刷新列表并回落**，不是把错误横幅一直挂着。
 */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}
