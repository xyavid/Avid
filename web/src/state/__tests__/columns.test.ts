/**
 * 两侧列宽的夹取规则用例。
 *
 * 宽度是用户拖出来的偏好，但不能把对话列挤没：实际渲染前一律过 `clampWidths`——
 * 先按各自的上下限夹，再按窗口宽度让位（先收右列，再收左列，各自不低于下限）。
 * 纯函数，不需要 DOM。
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
    // 900 - 520 = 380 的预算里，左列想留 300；右列在场时这笔账会被算进去
    const got = clampWidths({ sidebar: 300, rail: 400 }, 900, false)

    expect(got.sidebar).toBe(300)
    expect(got.rail).toBe(400) // 值还在存储里，只是不渲染
  })

  it('窗口变窄：先收右列，收到底再收左列', () => {
    // 预算 = 1000 - MAIN_MIN = 480；想留 240 + 280 = 520，超 40
    const got = clampWidths({ sidebar: 240, rail: 280 }, 1000, true)

    expect(got.sidebar).toBe(240) // 左列先不动
    expect(got.rail).toBe(280 - 40)
    expect(got.sidebar + got.rail).toBe(1000 - MAIN_MIN_WIDTH)
  })

  it('右列让到下限还不够，剩下的由左列出（只出该出的那部分）', () => {
    // 预算 = 950 - 520 = 430；两列想留 580，超 150：右列让 60 到底，左列出 90
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
