/**
 * 事件流的**决策策略**：纯函数，不碰 DOM、不碰 store。
 *
 * 从 `routes/useRunStream.tsx` 抽出来的原因：那个文件把"订阅、游标、合并、分发、
 * 降级轮询、对账"六件事揉在 170 行里，而其中真正有分支可错的只有几个判断
 * （什么时候该对账、降级后下一次轮询等多久、终止事件到达前 delta 该 flush 还是
 * cancel）。抽成纯函数之后这些判断能脱离浏览器单测，接线那一层只剩"调用"。
 */

import type { RunStatus } from '../api/types'
import type { RunPhase } from './reducer'
import { TERMINAL_EVENT_TYPES } from './types'
import type { AvidEventType } from './types'

/** 事件流静默多久就与注册表对账（I13，取自 OpenHands 的 30s 兜底）。 */
export const RECONCILE_SILENCE_MS = 30_000
/** 降级轮询：1s 起步，随运行时长退避到 15s（不是默认路径）。 */
export const POLL_BASE_MS = 1_000
export const POLL_MAX_MS = 15_000
/** 正常状态下与注册表对账的间隔。 */
export const STATUS_POLL_MS = 5_000
/** 退避的步长：每 10s 翻一倍。 */
export const POLL_STEP_MS = 10_000

/** 降级时的下一次轮询间隔：按运行已持续时长指数退避，封顶 15s。 */
export function pollDelay(startedAt: number, now: number = Date.now()): number {
  const elapsed = Math.floor((now - startedAt) / POLL_STEP_MS)
  return Math.min(POLL_BASE_MS * 2 ** elapsed, POLL_MAX_MS)
}

/** 未降级时按固定节奏对账；降级时按退避节奏。 */
export function nextReconcileDelay(
  degraded: boolean,
  startedAt: number | null,
  now: number = Date.now(),
): number {
  return degraded ? pollDelay(startedAt ?? now, now) : STATUS_POLL_MS
}

/** 本地视图是否已经进入终态（结束/失败/取消）。 */
export const SETTLED_PHASES: readonly RunPhase[] = ['done', 'failed', 'cancelled']

export function isSettled(phase: RunPhase): boolean {
  return SETTLED_PHASES.includes(phase)
}

export type ReconcileAction = 'settled' | 'silent' | 'none'

/**
 * 拿到 `GET /runs/{id}` 之后该做什么。
 *
 * - `settled`：注册表说这次运行已经结束——事件流可能漏了终态，按它收敛；
 * - `silent`：还在跑，但事件流静默超过阈值——可能断在半路，重取一次；
 * - `none`：一切正常，什么都不做（避免每次对账都打查询）。
 *
 * **权威终止不在流里**（I13）：流可能断在终态之前，所以这个判断必须存在。
 */
export function reconcileAction(
  status: RunStatus,
  lastEventAt: number,
  now: number = Date.now(),
): ReconcileAction {
  if (status !== 'running' && status !== 'awaiting_approval') return 'settled'
  const silentFor = now - (lastEventAt || now)
  return silentFor > RECONCILE_SILENCE_MS ? 'silent' : 'none'
}

export type DeltaAction = 'cancel' | 'flush'

/**
 * durable 事件到达时对"待落地 delta"的动作（I12）。
 *
 * 终止类要 **cancel**（别让乐观文本盖住最终结果），其余 durable 要 **flush**
 * （把已生成的增量先落上去，再渲染这条 durable）。两者不混用。
 */
export function deltaAction(type: string): DeltaAction {
  return TERMINAL_EVENT_TYPES.includes(type as AvidEventType) ? 'cancel' : 'flush'
}
