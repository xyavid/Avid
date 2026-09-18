/**
 * 事件流决策的单测。
 *
 * 这些判断原本揉在 `routes/useRunStream.tsx` 的 effect 里，只能靠 e2e（需要真实
 * 内核）间接覆盖——而"什么时候该对账、降级后等多久、终止前 delta 该 cancel 还是
 * flush"恰恰是接线里最容易错的部分。抽成纯函数之后当场可测。
 */

import { describe, expect, it } from 'vitest'

import {
  POLL_BASE_MS,
  POLL_MAX_MS,
  POLL_STEP_MS,
  RECONCILE_SILENCE_MS,
  STATUS_POLL_MS,
  deltaAction,
  isSettled,
  nextReconcileDelay,
  pollDelay,
  reconcileAction,
} from '../streamPolicy'

describe('pollDelay：降级轮询的退避', () => {
  it('从 1s 起步，每 10s 翻一倍', () => {
    const start = 1_000_000
    expect(pollDelay(start, start)).toBe(POLL_BASE_MS)
    expect(pollDelay(start, start + POLL_STEP_MS)).toBe(POLL_BASE_MS * 2)
    expect(pollDelay(start, start + POLL_STEP_MS * 2)).toBe(POLL_BASE_MS * 4)
  })

  it('封顶 15s，运行再久也不再涨', () => {
    const start = 1_000_000
    expect(pollDelay(start, start + POLL_STEP_MS * 4)).toBe(POLL_MAX_MS)
    expect(pollDelay(start, start + POLL_STEP_MS * 100)).toBe(POLL_MAX_MS)
  })
})

describe('nextReconcileDelay：正常节奏 vs 降级节奏', () => {
  it('正常 5s；降级按退避（首拍 1s）', () => {
    expect(nextReconcileDelay(false, null)).toBe(STATUS_POLL_MS)
    expect(nextReconcileDelay(true, null)).toBe(POLL_BASE_MS)
    expect(nextReconcileDelay(true, Date.now() - POLL_STEP_MS)).toBe(POLL_BASE_MS * 2)
  })
})

describe('reconcileAction：拿到注册表状态之后做什么', () => {
  it('注册表说结束了 → settled（权威终止不在流里，I13）', () => {
    for (const status of ['finished', 'failed', 'cancelled'] as const) {
      expect(reconcileAction(status, Date.now())).toBe('settled')
    }
  })

  it('还在跑且事件流新鲜 → none（不白打查询）', () => {
    expect(reconcileAction('running', Date.now())).toBe('none')
    expect(reconcileAction('awaiting_approval', Date.now())).toBe('none')
  })

  it('还在跑但静默超过阈值 → silent（可能断在半路）', () => {
    const now = 10_000_000
    expect(reconcileAction('running', now - RECONCILE_SILENCE_MS - 1, now)).toBe('silent')
    expect(reconcileAction('running', now - RECONCILE_SILENCE_MS + 1, now)).toBe('none')
  })

  it('从没收到过事件（lastEventAt=0）不算静默', () => {
    expect(reconcileAction('running', 0, 5)).toBe('none')
  })
})

describe('deltaAction：durable 事件渲染前对待落地 delta 的动作（I12）', () => {
  it('终止类 cancel，其余 durable flush', () => {
    expect(deltaAction('run_finished')).toBe('cancel')
    expect(deltaAction('run_failed')).toBe('cancel')
    expect(deltaAction('run_cancelled')).toBe('cancel')
    expect(deltaAction('assistant_message')).toBe('flush')
    expect(deltaAction('tool_call_started')).toBe('flush')
  })
})

describe('isSettled：本地视图是否已进入终态', () => {
  it('只有 done/failed/cancelled 算终态', () => {
    expect(isSettled('done')).toBe(true)
    expect(isSettled('failed')).toBe(true)
    expect(isSettled('cancelled')).toBe(true)
    expect(isSettled('streaming')).toBe(false)
    expect(isSettled('awaiting_approval')).toBe(false)
    expect(isSettled('cancelling')).toBe(false)
  })
})
