/**
 * `isActivePhase` 是「哪些阶段算还在跑」的唯一事实源，所以这里把 `RunPhase` 的取值
 * 覆盖一遍，并加一道**编译期**穷举检查：将来给 `RunPhase` 加阶段时，编译会失败，
 * 逼着改动的人先回答"它算不算还在跑"，而不是让两个消费者各自猜。
 */

import { describe, expect, it } from 'vitest'

import type { RunPhase } from '../../../../events/reducer'
import { isActivePhase } from '../phase'

const ACTIVE = ['submitting', 'streaming', 'awaiting_approval', 'cancelling'] as const
const SETTLED = ['idle', 'done', 'failed', 'cancelled'] as const

type Classified = (typeof ACTIVE)[number] | (typeof SETTLED)[number]

/**
 * 穷举检查：`RunPhase` 里出现这两组之外的成员时，下面这行不再类型成立
 * （`tsc -b --noEmit` 是 verify 的一道门禁，所以这个失败信号真的会到人面前）。
 */
const exhaustive: RunPhase extends Classified ? true : never = true

describe('isActivePhase：未收敛的四个阶段', () => {
  it('两组覆盖了 RunPhase 的每一个取值', () => {
    expect(exhaustive).toBe(true)
  })

  it.each(ACTIVE)('%s 算还在跑', (phase) => {
    expect(isActivePhase(phase)).toBe(true)
  })

  it.each(SETTLED)('%s 不算还在跑', (phase) => {
    expect(isActivePhase(phase)).toBe(false)
  })
})
