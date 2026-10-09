/**
 * Clamping rules for the side column widths. Widths are a dragged preference, but every render
 * passes them through `clampWidths`: clamp per column, then yield by viewport width (right column
 * first, left second, never below the minimums). Pure functions, no DOM needed.
 */

import { describe, expect, it } from 'vitest'

import { COLUMN_LIMITS, MAIN_MIN_WIDTH, clampWidths } from '../columns'

const wide = 1600

describe('列宽夹取', () => {
  it('窗口够宽时只按各自上下限夹', () => {
    const got = clampWidths({ sidebar: 100, rail: 900 }, wide, true)

    expect(got.sidebar).toBe(COLUMN_LIMITS.sidebar.min)
    expect(got.rail).toBe(COLUMN_LIMITS.rail.max)
  })

  it('没有右列时不占预算：只按上下限夹，左列不被挤', () => {
    // Budget 900 - 520 = 380; the left wants 300 and is not charged the rail's share
    const got = clampWidths({ sidebar: 300, rail: 400 }, 900, false)

    expect(got.sidebar).toBe(300)
    expect(got.rail).toBe(400) // value stays in storage, just not rendered
  })

  it('窗口变窄：先收右列，收到底再收左列', () => {
    // Budget = 1000 - MAIN_MIN = 480; wants 240 + 280 = 520, over by 40
    const got = clampWidths({ sidebar: 240, rail: 280 }, 1000, true)

    expect(got.sidebar).toBe(240) // left column untouched first
    expect(got.rail).toBe(280 - 40)
    expect(got.sidebar + got.rail).toBe(1000 - MAIN_MIN_WIDTH)
  })

  it('右列让到下限还不够，剩下的由左列出（只出该出的那部分）', () => {
    // Budget 950 - 520 = 430; wants 580, over by 150: rail yields 60, sidebar gives 90
    const got = clampWidths({ sidebar: 300, rail: 280 }, 950, true)

    expect(got.rail).toBe(COLUMN_LIMITS.rail.min)
    expect(got.sidebar).toBe(300 - 90)
    expect(got.sidebar + got.rail).toBe(950 - MAIN_MIN_WIDTH)
  })

  it('两列都到了下限还放不下：下限优先，主列自己扛（不返回负数）', () => {
    const got = clampWidths({ sidebar: 300, rail: 280 }, 800, true)

    expect(got.sidebar).toBe(COLUMN_LIMITS.sidebar.min)
    expect(got.rail).toBe(COLUMN_LIMITS.rail.min)
  })

  it('非整数与 NaN 都收敛成整数（拖动给的是浮点）', () => {
    expect(clampWidths({ sidebar: 240.6, rail: Number.NaN }, wide, true)).toEqual({
      sidebar: 241,
      rail: COLUMN_LIMITS.rail.min,
    })
  })
})
