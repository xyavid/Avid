/**
 * 事件流的**决策策略**：纯函数，不碰 DOM、不碰 store、不读时钟（`now` 是入参）。
 *
 * 为什么把它抽出来：订阅、游标、合并、分发、降级轮询、对账这六件事里，真正有分支可错的
 * 只有几个判断——什么时候该订阅 delta、什么时候该从流切到轮询、下一次对账等多久。
 * 抽成纯函数之后这些判断能脱离浏览器单测，接线那一层只剩"按返回值调用"。
 *
 * 输入是两类事实：
 *   · `Meta` 里的静态事实（`features.deltas` 声明 delta 通道是否存在；
 *     `stream.heartbeat_seconds` / `terminal_fallback_seconds` 是两个时间预算）；
 *   · 运行时信号（当前 phase、最后一次事件时间、是否已被上游标记降级、页面是否在后台）。
 *
 * **按特性分支，不按版本号分支**（`docs/guide/web-ui.md` §2）：版本号会随任意改动跳动，
 * 特性位才是"这条通道现在能不能用"的答案。
 */

import type { Meta } from '../api/types'
import type { RunPhase } from './reducer'

/** 降级轮询的起步间隔。 */
export const DEFAULT_POLL_MS = 1_000
/** 降级轮询的上界：再久也不会比 15s 更慢，否则一次卡住的运行要等太久才被发现。 */
export const MAX_POLL_MS = 15_000
/** 退避步长：静默每多这么久，轮询间隔翻一倍。 */
export const POLL_STEP_MS = 10_000

/** 已经结束的 phase：不会再有事件。 */
export const SETTLED_PHASES: readonly RunPhase[] = ['finished', 'failed', 'cancelled']

export interface StreamPolicyInput {
  /** `GET /api/meta` 的 `stream`：两个时间预算与重放缓冲大小。 */
  stream: Meta['stream']
  /** `GET /api/meta` 的 `features`：`deltas` 为 1 才订阅增量。 */
  features: Meta['features']
  phase: RunPhase
  /** 最后一次收到事件的时间戳（毫秒）；0 = 还没收到过。 */
  lastEventAt: number
  now: number
  /** 上游已经判定事件流不可用（`degraded` 信号）。 */
  degraded: boolean
  /** 页面是否在后台（`document.hidden`）；只影响 delta，不影响事件流。 */
  hidden?: boolean
}

export interface StreamPolicy {
  /** 是否维持 SSE 订阅。 */
  subscribe: boolean
  /** 是否带 `?deltas=1` 订阅增量。 */
  deltas: boolean
  mode: 'stream' | 'poll' | 'idle'
  /** 还差多久没收到事件就该与运行注册表对账；0 = 不需要（终态 / 已在轮询）。 */
  reconcileAfterMs: number
  /** 降级轮询间隔；`mode !== 'poll'` 时为 null。 */
  pollIntervalMs: number | null
  /** 每个判断的理由，便于把"为什么这样做"带进日志或调试面板。 */
  reasons: string[]
}

/**
 * 降级轮询间隔：刚降级时 1s，之后每静默 10s 翻一倍，封顶 15s。
 *
 * 与事件流的退避不同：这里退避的依据是"已经静默多久"而不是"失败了几次"——
 * 运行可能在长时间工具调用里什么都不发，这种静默不需要更快的轮询，也不需要更慢。
 * `sinceMs` 是兜底阈值：静默本身已经把阈值吃掉了，只有超出阈值的部分才计入退避。
 */
export function pollIntervalMs(silentForMs: number, sinceMs = 0): number {
  const excess = Math.max(0, silentForMs - sinceMs)
  const step = Math.floor(excess / POLL_STEP_MS)
  return Math.min(DEFAULT_POLL_MS * 2 ** step, MAX_POLL_MS)
}

export function streamPolicy(input: StreamPolicyInput): StreamPolicy {
  const reasons: string[] = []

  if (SETTLED_PHASES.includes(input.phase)) {
    // 事件不会再来：继续订阅等于白占一条 SSE 连接（服务端上限 24 条，标签页多了会打满）。
    return {
      subscribe: false,
      deltas: false,
      mode: 'idle',
      reconcileAfterMs: 0,
      pollIntervalMs: null,
      reasons: [`phase=${input.phase}：运行已进入终态，事件流不会再有新事件`],
    }
  }

  const heartbeatMs = input.stream.heartbeat_seconds * 1_000
  // 兜底阈值不能小于心跳：否则每一跳之间都会被误判成"静默"。
  const fallbackMs = Math.max(input.stream.terminal_fallback_seconds * 1_000, heartbeatMs, 1)

  // `lastEventAt === 0` 表示还没收到过任何事件（run_started 之前）：不算静默，
  // 否则刚订阅就被判成"断在半路"。这是唯一需要区分 0 与"很久以前"的地方。
  const silentForMs = input.lastEventAt > 0 ? Math.max(0, input.now - input.lastEventAt) : 0
  const silent = input.lastEventAt > 0 && silentForMs >= fallbackMs

  const polling = input.degraded || silent
  if (input.degraded) reasons.push('上游已判定事件流降级：改用注册表轮询')
  if (silent) reasons.push(`事件流静默 ${silentForMs}ms，超过兜底阈值 ${fallbackMs}ms`)

  if (polling) {
    return {
      subscribe: false,
      deltas: false,
      mode: 'poll',
      reconcileAfterMs: 0,
      pollIntervalMs: pollIntervalMs(silentForMs, fallbackMs),
      reasons,
    }
  }

  // delta 是"可任意丢"的锦上添花：特性没开、页面在后台、或不在生成正文（等审批）时都不订阅。
  const deltasSupported = input.features.deltas === 1
  if (!deltasSupported) reasons.push('features.deltas 未开启：按契约不带 ?deltas=1')
  if (input.hidden) reasons.push('页面在后台：增量看不到，不订阅 delta')
  if (input.phase !== 'running') reasons.push(`phase=${input.phase}：不在生成正文，无 delta 可订阅`)
  // 流本身保留（即使在后台）：断掉会丢掉整段 durable 重放点，回前台要重新拉历史。
  const deltas = deltasSupported && !input.hidden && input.phase === 'running'

  return {
    subscribe: true,
    deltas,
    mode: 'stream',
    // 还差多少才到兜底阈值：上层据此定下一次对账的定时器，而不是另写一份阈值。
    reconcileAfterMs: Math.max(0, fallbackMs - silentForMs),
    pollIntervalMs: null,
    reasons,
  }
}
