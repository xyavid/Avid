import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { request } from '../api/client'
import { subscribeRun } from '../api/stream'
import { useMeta } from '../api/queries'
import type { Run } from '../api/types'
import { createCoalescer } from '../events/coalescer'
import type { Coalescer } from '../events/coalescer'
import { TERMINAL_EVENT_TYPES } from '../events/types'
import { phaseFromStatus, runStoreActions, useRunStore } from '../state/runStore'

/** 事件流静默多久就与注册表对账（I13，取自发 OpenHands 的 30s 兜底）。 */
export const RECONCILE_SILENCE_MS = 30_000
/** 降级轮询：1s 起步，随运行时长退避到 15s（不是默认路径）。 */
export const POLL_BASE_MS = 1_000
export const POLL_MAX_MS = 15_000
const STATUS_POLL_MS = 5_000

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

function pollDelay(startedAt: number): number {
  const elapsed = Math.floor((Date.now() - startedAt) / 10_000)
  return Math.min(POLL_BASE_MS * 2 ** elapsed, POLL_MAX_MS)
}

/**
 * 事件流接线：**唯一**允许把事件写进活动域的地方（L4）。
 *
 * 三件事：
 *   1. 订阅 `subscribeRun`，delta 走合并器（每帧最多一次提交），durable 前置 flush、
 *      终止前置 cancel（I12）；
 *   2. `resync` → 标记视图待重建并重取条目；
 *   3. 静默 30s 或事件流降级 → `GET /runs/{id}` 对账（权威终止不在流里，I13），
 *      降级时切轮询并提示。
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
  const coalescer = useRef<Coalescer | null>(null)

  if (coalescer.current === null) {
    coalescer.current = createCoalescer({
      commit: (text) => runStoreActions.commitDelta(text),
    })
  }

  const refresh = useCallback(() => onResync(), [onResync])

  useEffect(() => {
    if (!runId) return
    const controller = new AbortController()
    setDegraded(false)
    setReconnectAttempt(null)

    const pump = async () => {
      const after = useRunStore.getState().view.seq
      try {
        for await (const signal of subscribeRun(runId, {
          after,
          deltas,
          signal: controller.signal,
        })) {
          if (signal.kind === 'event') {
            const event = signal.event
            if (event.type === 'assistant_delta') {
              coalescer.current?.push(String(event.data.text ?? ''))
              continue
            }
            if (TERMINAL_EVENT_TYPES.includes(event.type)) {
              coalescer.current?.cancel()
            } else {
              coalescer.current?.flush()
            }
            runStoreActions.apply(event)
            if (TERMINAL_EVENT_TYPES.includes(event.type)) refresh()
          } else if (signal.kind === 'resync') {
            runStoreActions.markDetached(true)
            refresh()
          } else if (signal.kind === 'reconnect') {
            setReconnectAttempt(signal.attempt)
          } else {
            setDegraded(true)
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setDegraded(true)
          console.error('[run-stream] 事件流中断', error)
        }
      }
    }

    void pump()
    return () => controller.abort()
  }, [runId, deltas, refresh])

  // 对账：权威终止以注册表 + 已提交条目为准。
  useEffect(() => {
    if (!runId) return undefined
    let stopped = false
    let timer: ReturnType<typeof setTimeout>

    const tick = async () => {
      const view = useRunStore.getState().view
      const settled = view.phase === 'done' || view.phase === 'failed' || view.phase === 'cancelled'
      if (!settled) {
        try {
          const run = await request<Run>(`/runs/${runId}`)
          const silent = Date.now() - (view.lastEventAt || Date.now())
          if (run.status !== 'running' && run.status !== 'awaiting_approval') {
            runStoreActions.markDetached(true)
            runStoreActions.setPhase(phaseFromStatus(run.status))
            refresh()
          } else if (silent > RECONCILE_SILENCE_MS) {
            refresh()
          }
        } catch (error) {
          // 404：内核重启过，run 已经不存在——按条目重建视图（§7.6）。
          runStoreActions.markDetached(true)
          refresh()
          console.error('[run-stream] 对账失败', error)
        }
      }
      if (!stopped) {
        timer = setTimeout(tick, degraded ? pollDelay(view.startedAt ?? Date.now()) : STATUS_POLL_MS)
      }
    }

    timer = setTimeout(tick, degraded ? POLL_BASE_MS : STATUS_POLL_MS)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [runId, degraded, refresh])

  const value = useMemo<RunStreamState>(
    () => ({ degraded, reconnectAttempt, refresh }),
    [degraded, reconnectAttempt, refresh],
  )

  return <RunStreamContext.Provider value={value}>{children}</RunStreamContext.Provider>
}

