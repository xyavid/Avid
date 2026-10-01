/**
 * 订阅策略：纯函数，输入 `Meta` 的两块事实 + 运行时信号，输出"订阅什么、怎么降级"。
 *
 * 先列失败清单（AGENTS.md §6）：
 *   F1 终态之后还在订阅：事件不会再来，白占一条 SSE 连接（服务端上限 24 条）
 *   F2 按 `features.deltas` 分支而不是按版本号猜：特性关掉时必须不带 `?deltas=1`
 *   F3 页面在后台时仍订阅 delta：文字看不到，白耗带宽与 CPU（但流本身要保留，
 *      否则回前台要整段重放）
 *   F4 事件流静默超过 `terminal_fallback_seconds` 却不降级：断在半路的流会一直"看起来在跑"
 *   F5 已经 degraded 了还被劝回流模式：降级是上层给的显式信号，不能被"刚收到过事件"抵消
 *   F6 降级后的轮询间隔没有退避：要么打得太密，要么静默越久反而越快
 *   F7 等待审批时订阅 delta：那一轮没有正文增量
 */

import { describe, expect, it } from 'vitest'

import { MAX_POLL_MS, pollIntervalMs, streamPolicy } from '../streamPolicy'
import type { StreamPolicyInput } from '../streamPolicy'
import type { Meta } from '../../api/types'

const STREAM: Meta['stream'] = {
  heartbeat_seconds: 15,
  terminal_fallback_seconds: 30,
  replay_buffer_size: 256,
}

function input(overrides: Partial<StreamPolicyInput> = {}): StreamPolicyInput {
  return {
    stream: STREAM,
    features: { deltas: 1 },
    phase: 'running',
    lastEventAt: 1_000,
    now: 2_000,
    degraded: false,
    ...overrides,
  }
}

describe('streamPolicy', () => {
  it('F1：终态之后不再订阅，也不轮询', () => {
    for (const phase of ['finished', 'failed', 'cancelled'] as const) {
      const policy = streamPolicy(input({ phase }))

      expect(policy.subscribe).toBe(false)
      expect(policy.deltas).toBe(false)
      expect(policy.mode).toBe('idle')
      expect(policy.pollIntervalMs).toBeNull()
      expect(policy.reconcileAfterMs).toBe(0)
      expect(policy.reasons.length).toBeGreaterThan(0)
    }
  })

  it('正常运行：订阅事件流并订阅 delta，还给出静默对账的剩余时间', () => {
    const policy = streamPolicy(input())

    expect(policy).toMatchObject({ subscribe: true, deltas: true, mode: 'stream', pollIntervalMs: null })
    // 静默 1s，兜底阈值 30s → 还剩 29s 才需要与注册表对账。
    expect(policy.reconcileAfterMs).toBe(29_000)
  })

  it('F2：features 没有 deltas 时不订阅 delta（按特性分支，不按版本号）', () => {
    const policy = streamPolicy(input({ features: {} }))

    expect(policy.deltas).toBe(false)
    expect(policy.mode).toBe('stream')
    expect(policy.reasons.join()).toContain('deltas')
  })

  it('F3：页面在后台时不订阅 delta，但流本身保留', () => {
    const policy = streamPolicy(input({ hidden: true }))

    expect(policy.deltas).toBe(false)
    expect(policy.subscribe).toBe(true)
    expect(policy.mode).toBe('stream')
  })

  it('F7：等待审批时不订阅 delta', () => {
    const policy = streamPolicy(input({ phase: 'awaiting_approval' }))

    expect(policy.deltas).toBe(false)
    expect(policy.subscribe).toBe(true)
  })

  it('F4：静默超过兜底阈值就降级轮询', () => {
    const policy = streamPolicy(input({ lastEventAt: 1_000, now: 31_000 }))

    expect(policy.mode).toBe('poll')
    expect(policy.subscribe).toBe(false)
    expect(policy.deltas).toBe(false)
    expect(policy.pollIntervalMs).toBe(1_000)
    expect(policy.reasons.join()).toContain('静默')
  })

  it('F4：还没收到过任何事件时不算静默（run_started 之前不该定罪）', () => {
    const policy = streamPolicy(input({ lastEventAt: 0, now: 999_999 }))

    expect(policy.mode).toBe('stream')
  })

  it('F5：degraded 是显式信号，刚收到过事件也仍然轮询', () => {
    const policy = streamPolicy(input({ degraded: true }))

    expect(policy.mode).toBe('poll')
    expect(policy.pollIntervalMs).toBe(1_000)
    expect(policy.reasons.join()).toContain('降级')
  })

  it('F6：轮询间隔随静默时长退避并封顶', () => {
    expect(pollIntervalMs(0, 30_000)).toBe(1_000)
    expect(pollIntervalMs(30_000, 30_000)).toBe(1_000)
    expect(pollIntervalMs(50_000, 30_000)).toBe(4_000)
    expect(pollIntervalMs(600_000, 30_000)).toBe(MAX_POLL_MS)

    const longSilence = streamPolicy(input({ lastEventAt: 1_000, now: 601_000 }))
    expect(longSilence.pollIntervalMs).toBe(MAX_POLL_MS)
  })
})
