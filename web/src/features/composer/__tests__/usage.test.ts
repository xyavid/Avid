/**
 * 用量展示口径的纯函数用例：合并规则与数字格式。
 *
 * 这里不碰 React：`pickUsage` 的优先级与 `formatTokens` 的边界（null / 负数 / 进位）
 * 是界面正确性的全部来源，组件只负责把结果拼进文案。
 */

import { describe, expect, it } from 'vitest'

import type { UsageReport } from '../../../api/types'
import { formatPercent, formatTokens, pickUsage } from '../lib/usage'

function report(tokens: number | null): UsageReport {
  return {
    context: { tokens, window: 200_000, utilization: null },
    cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
  }
}

describe('pickUsage', () => {
  it('活动域优先：运行中的实时读数盖过落盘的那一份', () => {
    const live = report(1000)
    const saved = report(9999)
    expect(pickUsage(live, saved)).toBe(live)
  })

  it('活动域为空时回落到落盘值（刷新 / 切会话 / 切分支都靠它）', () => {
    const saved = report(9999)
    expect(pickUsage(null, saved)).toBe(saved)
  })

  it('两个域都没有读数时是 null，而不是一个零值报告', () => {
    expect(pickUsage(null, null)).toBeNull()
    expect(pickUsage(null, undefined)).toBeNull()
  })
})

describe('formatTokens', () => {
  it('按量级换单位，整数不带小数尾巴', () => {
    expect(formatTokens(0)).toBe('0')
    expect(formatTokens(999)).toBe('999')
    expect(formatTokens(1234)).toBe('1.2k')
    expect(formatTokens(72_000)).toBe('72k')
    expect(formatTokens(200_000)).toBe('200k')
    expect(formatTokens(1_200_000)).toBe('1.2M')
  })

  it('null 与非有限数都显示「—」：没有这个数不等于 0', () => {
    expect(formatTokens(null)).toBe('—')
    expect(formatTokens(Number.NaN)).toBe('—')
    expect(formatTokens(Number.POSITIVE_INFINITY)).toBe('—')
  })

  it('负数保留符号（上游谎报时不去猜它想说什么）', () => {
    expect(formatTokens(-1500)).toBe('-1.5k')
  })
})

describe('formatPercent', () => {
  it('四舍五入到整数百分比', () => {
    expect(formatPercent(0)).toBe('0%')
    expect(formatPercent(0.36)).toBe('36%')
    expect(formatPercent(0.7778)).toBe('78%')
    expect(formatPercent(1)).toBe('100%')
  })

  it('null 显示「—」：没有命中数据与命中率 0% 是两件事', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(Number.NaN)).toBe('—')
  })
})
