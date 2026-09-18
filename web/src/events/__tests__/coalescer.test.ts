/**
 * C4：delta 合并器 ≤1 commit/帧。调度器是注入的，所以这里用假的 rAF 数帧，
 * 不需要浏览器，也不需要等真实的 16ms。
 */

import { describe, expect, it } from 'vitest'

import { createCoalescer } from '../coalescer'
import type { Coalescer } from '../coalescer'

interface FakeScheduler {
  schedule: (callback: () => void) => number
  cancelSchedule: (handle: number) => void
  /** 手动推进一帧：只执行本帧开始前已经排队（且没被取消）的回调。 */
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
  it('200 条 delta 挤在同一帧里也只 commit 一次', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 200; i += 1) coalescer.push(`d${i};`)
    scheduler.tick()

    expect(commits).toHaveLength(1)
    expect(commits[0]).toBe(Array.from({ length: 200 }, (_, i) => `d${i};`).join(''))
    // 每帧最多一次 → commit 次数不超过帧数。
    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
  })

  it('跨多帧时 commit 次数 ≤ 帧数，且文本一条不丢', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 200; i += 1) {
      coalescer.push('x')
      if (i % 10 === 9) scheduler.tick()
    }
    scheduler.tick()

    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
    expect(commits).toHaveLength(20)
    expect(commits.join('')).toBe('x'.repeat(200))
  })

  it('flush() 立刻提交、清空 pending，已排队的帧回调不再触发', () => {
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

  it('cancel() 丢弃已攒内容，之后 tick 不提交', () => {
    const { scheduler, commits, coalescer } = setup()

    coalescer.push('drop me')
    coalescer.cancel()

    expect(coalescer.pending()).toBe('')
    expect(scheduler.queued()).toBe(0)
    scheduler.tick()
    expect(commits).toEqual([])
  })

  it('1000 条 delta 在每帧 tick 一次下 commit 次数 ≤ 帧数', () => {
    const { scheduler, commits, coalescer } = setup()

    for (let i = 0; i < 1000; i += 1) {
      coalescer.push('z')
      if (i % 10 === 9) scheduler.tick() // 每 10 条一帧
    }
    scheduler.tick()

    expect(scheduler.frames()).toBe(101)
    expect(commits.length).toBeLessThanOrEqual(scheduler.frames())
    expect(commits.length).toBe(100)
    expect(commits.length).toBeLessThan(1000)
    expect(commits.join('')).toBe('z'.repeat(1000))
  })
})
