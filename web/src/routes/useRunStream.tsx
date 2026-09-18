import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { useMeta } from '../api/queries'

import { useEventStream } from './useEventStream'
import { useReconcile } from './useReconcile'

/** 降级/重连状态：由 provider 提供给页面（页面据此显示提示条与"重试"按钮）。 */
export interface RunStreamState {
  degraded: boolean
  reconnectAttempt: number | null
  refresh: () => void
}

const RunStreamContext = createContext<RunStreamState>({
  degraded: false,
  reconnectAttempt: null,
  refresh: () => undefined,
})

export function useRunStreamState(): RunStreamState {
  return useContext(RunStreamContext)
}

/**
 * 事件流接线（L4）：**唯一**允许把事件写进活动域的地方。
 *
 * 这里只做组合，三件事各自在别处：
 *   1. 订阅与分发 → `useEventStream`（含 delta 合并与 I12 的 flush/cancel）；
 *   2. 对账与降级 → `useReconcile`（权威终止不在流里，I13）；
 *   3. 游标、重连与退避策略 → `api/stream` + `events/streamPolicy`（纯函数，有单测）。
 *
 * `onResync` 由 route 传进来（只有它知道怎么重取条目与分支）；provider 不碰查询。
 */
export function RunStreamProvider({
  runId,
  onResync,
  children,
}: {
  runId: string | null
  onResync: () => void
  children: ReactNode
}) {
  const meta = useMeta()
  const deltas = meta.data?.features.deltas === 1
  const [degraded, setDegraded] = useState(false)
  const [reconnectAttempt, setReconnectAttempt] = useState<number | null>(null)

  const refresh = useCallback(() => onResync(), [onResync])
  const markDegraded = useCallback(() => setDegraded(true), [])
  const markReconnect = useCallback((attempt: number) => setReconnectAttempt(attempt), [])

  // runId 变了（切会话）就清掉上一条流的降级/重连标记。写在订阅 effect 之前，
  // 于是同一轮里先是"重置"再是"订阅"。
  useEffect(() => {
    setDegraded(false)
    setReconnectAttempt(null)
  }, [runId])

  useEventStream({
    runId,
    deltas,
    refresh,
    onDegraded: markDegraded,
    onReconnect: markReconnect,
  })
  useReconcile({ runId, degraded, refresh })

  const value = useMemo<RunStreamState>(
    () => ({ degraded, reconnectAttempt, refresh }),
    [degraded, reconnectAttempt, refresh],
  )

  return <RunStreamContext.Provider value={value}>{children}</RunStreamContext.Provider>
}
