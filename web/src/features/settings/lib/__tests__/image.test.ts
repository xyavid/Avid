/**
 * `fitWithin` 是缩放规则的唯一实现，所以用例只压边界：**不放大**、等比、以及非有限输入。
 * 非有限输入必须给出 0×0 而不是 NaN——调用方据此拒绝，否则会渲染一个畸形 canvas。
 */

import { describe, expect, it } from 'vitest'

import { BACKDROP_MAX_CHARS, exceedsBackdropLimit, fitWithin } from '../image'

describe('fitWithin：等比缩到最大边长以内', () => {
  it('大图按长边缩到 max', () => {
    expect(fitWithin(4000, 3000, 1920)).toEqual({ width: 1920, height: 1440 })
  })

  it('竖图按长边（高）缩', () => {
    expect(fitWithin(3000, 4000, 1920)).toEqual({ width: 1440, height: 1920 })
  })

  it('小图不放大（放大会变糊而且更占地方）', () => {
    expect(fitWithin(320, 200, 1920)).toEqual({ width: 320, height: 200 })
  })

  it('正方形取满', () => {
    expect(fitWithin(1000, 1000, 512)).toEqual({ width: 512, height: 512 })
  })

  it('刚好在上限上不算超（边界取闭区间）', () => {
    const exact = 'x'.repeat(BACKDROP_MAX_CHARS)
    expect(exceedsBackdropLimit(exact)).toBe(false)
    expect(exceedsBackdropLimit(`${exact}x`)).toBe(true)
  })

  it('非有限或非正尺寸给 0×0，不给 NaN', () => {
    for (const [w, h] of [
      [0, 100],
      [100, 0],
      [-5, 100],
      [Number.NaN, 100],
      [100, Number.POSITIVE_INFINITY],
    ]) {
      expect(fitWithin(w as number, h as number, 1920)).toEqual({ width: 0, height: 0 })
    }
  })
})
