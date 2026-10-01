/**
 * 合并器：把高频 delta 攒成每帧一次 `commit`。
 *
 * 先列失败清单（AGENTS.md §6）：
 *   F1 同一帧里 N 条 delta 只能 commit 一次（否则每 token 一次 setState，流式期卡顿）
 *   F2 跨多帧时文本一条不丢，且 commit 次数 ≤ 帧数
 *   F3 `flush()` 立刻提交并撤销已排队的帧回调（不能提交两次）
 *   F4 `cancel()` 丢掉已攒内容，之后 tick 不提交（终止事件前的对偶动作）
 *   F5 调度器与时钟都是注入的：模块不能直接依赖真实 rAF（node 环境没有它）
 */

import { describe, expect, it } from 'vitest'

import { createCoalescer } from '../coalescer'
import type { Coalescer } from '../coalescer'

interface FakeScheduler {
  schedule: (callback: () => void) => number
  cancelSchedule: (handle: number) => void
  /** 手动推进一帧：只执行本帧开始前已排队且未被取消的回调。 */
  tick: () => void
  frames: () => number
  queued: () => number
}

function fakeScheduler(): FakeScheduler {
  let sequence = 0
  let frameCount = 0
  const pending = new Map<number, () => void>()
  return {
    schedule: (callback) => {
      sequence += 1
      pending.set(sequence, callback)
      return sequence
    },
    cancelSchedule: (handle) => {
      pending.delete(handle)
    },
    tick: () => {
      frameCount += 1
      const due = [...pending.values()]
      pending.clear()
      for (const callback of due) callback()
    },
    frames: () => frameCount,
    queued: () => pending.size,
  }
}

function setup(): { scheduler: FakeScheduler; commits: string[]; coalescer: Coalescer } {
  const scheduler = fakeScheduler()
  const commits: string[] = []
  const coalescer = createCoalescer({
    commit: (text) => commits.push(text),
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancelSchedule,
  })
  return { scheduler, commits, coalescer }
}

describe('createCoalescer', () => {
  it('F1：同一帧 200 条 delta 只 commit 一次，内容拼接完整', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 200; i += 1) coalescer.push(`d${i};`)
    scheduler.tick()

    expect(commits).toHaveLength(1)
    expect(commits[0]).toBe(Array.from({ length: 200 }, (_, i) => `d${i};`).join(''))
    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
  })

  it('F2：跨帧时 commit 次数 ≤ 帧数，文本一条不丢', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 200; i += 1) {
      coalescer.push('x')
      if (i % 10 === 9) scheduler.tick()
    }
    scheduler.tick()

    expect(commits).toHaveLength(20)
    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
    expect(commits.join('')).toBe('x'.repeat(200))
  })

  it('F3：flush() 立刻提交、清空 pending，已排队的回调不再重复提交', () => {
    const { scheduler, commits, coalescer } = setup()

    coalescer.push('a')
    coalescer.push('b')
    expect(coalescer.pending()).toBe('ab')
    expect(scheduler.queued()).toBe(1)

    coalescer.flush()

    expect(commits).toEqual(['ab'])
    expect(coalescer.pending()).toBe('')
    expect(scheduler.queued()).toBe(0)

    scheduler.tick()
    expect(commits).toEqual(['ab'])
  })

  it('F4：cancel() 丢弃已攒内容，之后 tick 不提交', () => {
    const { scheduler, commits, coalescer } = setup()

    coalescer.push('丢弃我')
    coalescer.cancel()

    expect(coalescer.pending()).toBe('')
    expect(scheduler.queued()).toBe(0)
    scheduler.tick()
    expect(commits).toEqual([])
  })

  it('空 push 不排帧，flush 空内容不 commit', () => {
    const { scheduler, commits, coalescer } = setup()

    coalescer.push('')
    expect(scheduler.queued()).toBe(0)
    coalescer.flush()
    expect(commits).toEqual([])
  })

  it('F2：1000 条 delta 每 10 条一帧，commit 次数远小于条数', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 1_000; i += 1) {
      coalescer.push('z')
      if (i % 10 === 9) scheduler.tick()
    }
    scheduler.tick()

    expect(commits).toHaveLength(100)
    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
    expect(commits.join('')).toBe('z'.repeat(1_000))
  })

  it('F5：不注入调度器时也能在无 rAF 的 node 环境里工作（回落定时器）', () => {
    const commits: string[] = []
    const coalescer = createCoalescer({ commit: (text) => commits.push(text) })

    coalescer.push('无调度器')
    expect(coalescer.pending()).toBe('无调度器')

    // 不依赖真实帧：直接 flush 走同步路径，任何时候都可用。
    coalescer.flush()
    expect(commits).toEqual(['无调度器'])
    coalescer.dispose()
  })
})
