/**
 * delta 合并器：rAF 批量提交，**≤1 commit/帧**（C4）。
 *
 * 调度器是注入的纯依赖，所以合并逻辑可以脱离浏览器单测（`coalescer.test.ts`
 * 用假的 rAF 数帧，断言 200 条 delta 的 commit 次数不超过帧数）。
 *
 * 与 reducer 的分工：合并器只负责「攒 + 每帧提交一次」，什么时候 flush / cancel
 * 由 reducer 在 durable / 终止事件处决定（不变量 I12）。两者不混用。
 */

export interface CoalescerOptions {
  commit: (text: string) => void
  schedule?: (callback: () => void) => number
  cancelSchedule?: (handle: number) => void
}

export interface Coalescer {
  push: (text: string) => void
  /** 把已攒的内容立刻落上去（durable 事件渲染前）。 */
  flush: () => void
  /** 丢掉已攒的内容（终止事件渲染前）。 */
  cancel: () => void
  pending: () => string
  dispose: () => void
}

export function createCoalescer(options: CoalescerOptions): Coalescer {
  const schedule = options.schedule ?? ((callback) => requestAnimationFrame(callback))
  const cancelSchedule = options.cancelSchedule ?? ((handle) => cancelAnimationFrame(handle))

  let buffer = ''
  let handle: number | null = null

  const drain = () => {
    handle = null
    if (!buffer) return
    const text = buffer
    buffer = ''
    options.commit(text)
  }

  const drop = () => {
    if (handle !== null) {
      cancelSchedule(handle)
      handle = null
    }
    buffer = ''
  }

  return {
    push: (text) => {
      if (!text) return
      buffer += text
      if (handle === null) handle = schedule(drain)
    },
    flush: () => {
      if (handle !== null) {
        cancelSchedule(handle)
        handle = null
      }
      drain()
    },
    cancel: drop,
    pending: () => buffer,
    dispose: drop,
  }
}
