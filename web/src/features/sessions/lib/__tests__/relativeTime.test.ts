/**
 * relativeTime 的边界：每一档的**下界**都要钉住，否则改动分档阈值时没人拦得住。
 * 时间戳全部相对一个固定的 now 给出，用例与跑测试的时刻无关。
 */

import { describe, expect, it } from 'vitest'

import { relativeTime } from '../relativeTime'

const NOW = Date.UTC(2024, 0, 10, 12, 0, 0)
const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

describe('relativeTime', () => {
  it('一分钟以内是「刚刚」', () => {
    expect(relativeTime(NOW, NOW)).toBe('刚刚')
    expect(relativeTime(NOW - 59_000, NOW)).toBe('刚刚')
  })

  it('分钟档', () => {
    expect(relativeTime(NOW - MINUTE, NOW)).toBe('1 分钟前')
    expect(relativeTime(NOW - 59 * MINUTE, NOW)).toBe('59 分钟前')
  })

  it('小时档', () => {
    expect(relativeTime(NOW - HOUR, NOW)).toBe('1 小时前')
    expect(relativeTime(NOW - 23 * HOUR, NOW)).toBe('23 小时前')
  })

  it('「昨天」只覆盖 24–48 小时', () => {
    expect(relativeTime(NOW - DAY, NOW)).toBe('昨天')
    expect(relativeTime(NOW - 2 * DAY + 1, NOW)).toBe('昨天')
    expect(relativeTime(NOW - 2 * DAY, NOW)).toBe('2 天前')
  })

  it('天档到一个月为止', () => {
    expect(relativeTime(NOW - 3 * DAY, NOW)).toBe('3 天前')
    expect(relativeTime(NOW - 29 * DAY, NOW)).toBe('29 天前')
  })

  it('超过一个月改给绝对日期', () => {
    expect(relativeTime(NOW - 400 * DAY, NOW)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('未来时间返回「刚刚」，不出现负数', () => {
    expect(relativeTime(NOW + 10 * MINUTE, NOW)).toBe('刚刚')
  })
})
