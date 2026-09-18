import { describe, expect, it } from 'vitest'

import { formatRelative } from '../lib/relativeTime'

const NOW = 1_700_000_000_000
const MINUTE = 60_000

describe('formatRelative', () => {
  it('不到一分钟算刚刚', () => {
    expect(formatRelative(NOW, NOW, 'zh-CN')).toEqual({ unit: 'now' })
    expect(formatRelative(NOW - 59_000, NOW, 'zh-CN')).toEqual({ unit: 'now' })
  })

  it('分钟 / 小时 / 天逐级进位', () => {
    expect(formatRelative(NOW - 16 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'minute', value: 16 })
    expect(formatRelative(NOW - 59 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'minute', value: 59 })
    expect(formatRelative(NOW - 60 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'hour', value: 1 })
    expect(formatRelative(NOW - 2 * 60 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'hour', value: 2 })
    expect(formatRelative(NOW - 23 * 60 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'hour', value: 23 })
    expect(formatRelative(NOW - 24 * 60 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'day', value: 1 })
    expect(formatRelative(NOW - 6 * 24 * 60 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'day', value: 6 })
  })

  it('超过一周改成日期', () => {
    const result = formatRelative(NOW - 8 * 24 * 60 * MINUTE, NOW, 'zh-CN')

    expect(result.unit).toBe('date')
    expect(result.unit === 'date' && result.text.length).toBeGreaterThan(0)
  })

  it('未来时间（时钟回拨 / 服务端有时差）显示刚刚，不出现负数', () => {
    expect(formatRelative(NOW + 5 * MINUTE, NOW, 'zh-CN')).toEqual({ unit: 'now' })
  })

  it('时间戳非法时也显示刚刚，而不是 NaN 分钟', () => {
    expect(formatRelative(Number.NaN, NOW, 'zh-CN')).toEqual({ unit: 'now' })
  })
})
