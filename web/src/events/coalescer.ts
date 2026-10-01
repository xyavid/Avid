/**
 * delta 合并器：攒 + 每帧至多一次 `commit`。
 *
 * 为什么要它：流式回答每秒能到达几十上百条 delta，逐条 `setState` 会把每一帧的工作
 * 从"渲染一次"变成"渲染 N 次"，而且每次都在同一帧里做（浏览器的帧边界与网络事件无关）。
 *
 * 与 reducer 的分工：合并器只管"攒 + 每帧提交一次"；什么时候 flush / 丢弃由 reducer
 * 在 durable / 终态事件处决定（不变量 I12）。两者不混用。
 *
 * **调度器是注入的纯依赖**：模块里不直接调用真实 `requestAnimationFrame`，这样合并逻辑
 * 能在默认 node 环境（没有 rAF）里用假调度器单测，不需要等真实的 16ms。
 */

export interface CoalescerOptions {
  /** 攒到一帧的内容一次性交出去。 */
  commit: (text: string) => void
  /** 默认用 `requestAnimationFrame`；没有它（node / 非浏览器环境）时回落到 16ms 定时器。 */
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

const hasAnimationFrame = (): boolean =>
  typeof globalThis.requestAnimationFrame === 'function' &&
  typeof globalThis.cancelAnimationFrame === 'function'

function defaultSchedule(callback: () => void): number {
  if (hasAnimationFrame()) return globalThis.requestAnimationFrame(callback)
  // 非浏览器环境（单测 / SSR 预渲染）没有帧：定时器给同样的"合批"语义。
  return setTimeout(callback, 16) as unknown as number
}

function defaultCancel(handle: number): void {
  if (hasAnimationFrame()) {
    globalThis.cancelAnimationFrame(handle)
    return
  }
  clearTimeout(handle)
}

export function createCoalescer(options: CoalescerOptions): Coalescer {
  const schedule = options.schedule ?? defaultSchedule
  const cancelSchedule = options.cancelSchedule ?? defaultCancel

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
      // 已经有排队的一帧就不再排：这就是"每帧至多一次"的实现（而不是每次 push 都排）。
      if (handle === null) handle = schedule(drain)
    },
    flush: () => {
      if (handle !== null) {
        cancelSchedule(handle)
        handle = null
      }
      // 走同一个 drain：已排队但未执行的内容与刚攒的一起提交，不会提交两次。
      drain()
    },
    cancel: drop,
    pending: () => buffer,
    dispose: drop,
  }
}
