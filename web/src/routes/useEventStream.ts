/**
 * 事件流接线：订阅一个 run 的 SSE，把帧喂给合并器与 reducer。
 *
 * 只做"订阅 + 分发"一件事——合并/落地由 `events/coalescer` 与 `events/reducer`
 * 负责，对账/降级由 `useReconcile` 负责，游标与重连策略在 `api/stream`。
 */

import { useEffect, useRef } from 'react'

import { subscribeRun } from '../api/stream'
import { createCoalescer } from '../events/coalescer'
import type { Coalescer } from '../events/coalescer'
import { deltaAction } from '../events/streamPolicy'
import { runStoreActions, useRunStore } from '../state/runStore'

export interface EventStreamOptions {
  runId: string | null
  /** 服务端是否声明了 deltas 能力（`features.deltas === 1`）。 */
  deltas: boolean
  /** resync / 终止事件之后要重取权威视图。 */
  refresh: () => void
  /** 进入降级（流断了、解析失败）时回调。 */
  onDegraded: () => void
  /** 重连尝试中的回调（attempt 从 1 递增）。 */
  onReconnect: (attempt: number) => void
}

/**
 * 订阅并分发。写完 `runStore` 的唯一入口仍在这里（L4），组件只读。
 *
 * 合并器用 ref 持有：它必须跨渲染存活（攒着的 delta 不能因为重渲染丢掉），
 * 但它的 `commit` 只依赖模块级的 `runStoreActions`，所以不需要在 effect 里重建。
 */
export function useEventStream({
  runId,
  deltas,
  refresh,
  onDegraded,
  onReconnect,
}: EventStreamOptions): void {
  const coalescer = useRef<Coalescer | null>(null)
  if (coalescer.current === null) {
    coalescer.current = createCoalescer({
      commit: (text) => runStoreActions.commitDelta(text),
    })
  }

  useEffect(() => {
    if (!runId) return
    const controller = new AbortController()

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
            // durable 事件渲染前：终止类丢弃待落地 delta，其余先 flush（I12）。
            if (deltaAction(event.type) === 'cancel') coalescer.current?.cancel()
            else coalescer.current?.flush()
            runStoreActions.apply(event)
            if (deltaAction(event.type) === 'cancel') refresh()
          } else if (signal.kind === 'resync') {
            runStoreActions.markDetached(true)
            refresh()
          } else if (signal.kind === 'reconnect') {
            onReconnect(signal.attempt)
          } else {
            onDegraded()
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          onDegraded()
          console.error('[run-stream] 事件流中断', error)
        }
      }
    }

    void pump()
    return () => controller.abort()
  }, [runId, deltas, refresh, onDegraded, onReconnect])
}
