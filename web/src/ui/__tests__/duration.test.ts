import { describe, expect, it } from 'vitest'

import { durationLabel, elapsedLabel } from '../duration'

describe('durationLabel（思考段与工具行的紧凑读数）', () => {
  it('毫秒 / 秒 / 分三档', () => {
    expect(durationLabel(320)).toBe('320ms')
    expect(durationLabel(3_200)).toBe('3.2s')
    expect(durationLabel(45_000)).toBe('45s')
    expect(durationLabel(75_000)).toBe('1m')
  })
})

describe('elapsedLabel（收尾折叠行的「用时」）', () => {
  it('不到一分钟说秒——不说 0 秒', () => {
    expect(elapsedLabel(1)).toBe('1秒')
    expect(elapsedLabel(45_600)).toBe('46秒')
  })

  it('进位：59.6 秒进到 1 分，且整分不带零头', () => {
    expect(elapsedLabel(59_600)).toBe('1分')
    expect(elapsedLabel(60_000)).toBe('1分')
    expect(elapsedLabel(791_000)).toBe('13分11秒')
  })

  it('一小时以上说小时与分', () => {
    expect(elapsedLabel(3_600_000)).toBe('1小时')
    expect(elapsedLabel(3_720_000)).toBe('1小时2分')
  })
})
