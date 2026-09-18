/**
 * 对账：事件流之外的**权威终止**来源（I13）。
 *
 * 流可能断在终态之前，"运行结束了"这件事因此不能只靠流。这个 hook 定时问一次
 * `GET /runs/{id}`：注册表说结束了就按它收敛；还在跑但流静默太久就重取一次权威
 * 视图；正常就什么都不做。降级（流断了）时轮询按退避节奏跑。
 */

import { useEffect } from 'react'

import { request } from '../api/client'
import type { Run } from '../api/types'
import { isSettled, nextReconcileDelay, reconcileAction } from '../events/streamPolicy'
import { phaseFromStatus, runStoreActions, useRunStore } from '../state/runStore'

export interface ReconcileOptions {
  runId: string | null
  /** 当前是否处于降级（流断了，改靠轮询）。 */
  degraded: boolean
  refresh: () => void
}

/** 按 5s（降级时退避）的节奏对账，直到本地视图进入终态。 */
export function useReconcile({ runId, degraded, refresh }: ReconcileOptions): void {
  useEffect(() => {
    if (!runId) return undefined
    let stopped = false
    let timer: ReturnType<typeof setTimeout>

    const tick = async () => {
      const view = useRunStore.getState().view
      if (!isSettled(view.phase)) {
        try {
          const run = await request<Run>(`/runs/${runId}`)
          const action = reconcileAction(run.status, view.lastEventAt)
          if (action === 'settled') {
            runStoreActions.markDetached(true)
            runStoreActions.setPhase(phaseFromStatus(run.status))
            refresh()
          } else if (action === 'silent') {
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
        timer = setTimeout(tick, nextReconcileDelay(degraded, view.startedAt))
      }
    }

    // 首拍：正常 5s；已降级则按退避的起点 1s（`nextReconcileDelay(true, null)`）。
    timer = setTimeout(tick, nextReconcileDelay(degraded, null))
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [runId, degraded, refresh])
}
