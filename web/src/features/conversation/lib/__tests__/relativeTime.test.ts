import { describe, expect, it } from 'vitest'

import { relativeTime } from '../relativeTime'

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR
const NOW = 1_800_000_000_000

describe('relativeTime', () => {
  it('同一时刻与未来时间都返回「刚刚」', () => {
    expect(relativeTime(NOW, NOW)).toBe('刚刚')
    // 客户端时钟比服务端快时时间戳会"在未来"，不能渲染成"还有 N 分钟"。
    expect(relativeTime(NOW + 5 * MINUTE, NOW)).toBe('刚刚')
    expect(relativeTime(Number.NaN, NOW)).toBe('刚刚')
  })

  it('不足一分钟是「刚刚」', () => {
    expect(relativeTime(NOW - 59 * SECOND, NOW)).toBe('刚刚')
  })

  it('分钟档的下边界是 60 秒', () => {
    expect(relativeTime(NOW - MINUTE, NOW)).toBe('1 分钟前')
    expect(relativeTime(NOW - 3 * MINUTE, NOW)).toBe('3 分钟前')
    expect(relativeTime(NOW - 59 * MINUTE, NOW)).toBe('59 分钟前')
  })

  it('小时档的下边界是 60 分钟', () => {
    expect(relativeTime(NOW - HOUR, NOW)).toBe('1 小时前')
    expect(relativeTime(NOW - 2 * HOUR, NOW)).toBe('2 小时前')
    expect(relativeTime(NOW - (24 * HOUR - 1), NOW)).toBe('23 小时前')
  })

  it('24 小时到 48 小时是「昨天」', () => {
    expect(relativeTime(NOW - DAY, NOW)).toBe('昨天')
    expect(relativeTime(NOW - (2 * DAY - 1), NOW)).toBe('昨天')
  })

  it('48 小时以上退回天数', () => {
    expect(relativeTime(NOW - 2 * DAY, NOW)).toBe('2 天前')
    expect(relativeTime(NOW - 3 * DAY, NOW)).toBe('3 天前')
  })
})
