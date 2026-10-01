/**
 * 用量格式化用例。核心只有一条：**null ≠ 0**。
 *
 * 报告 §3.2 与 `api/types.ts` 的注释都写明：可空字段的 null 表示"没有这个数"
 * （端点不上报 / 不知道窗口 / 没有分块数据），界面要显示「—」。这条如果失守，
 * 用户会看到"上下文占用 0%"这种看起来正常、实际是坏数据的读数。
 */

import type { UsageReport } from '../../../../api/types'
import { describe, expect, it } from 'vitest'

import { compactUsage, contextSegments } from '../usage'

function usageOf(overrides: {
  tokens?: number | null
  window?: number | null
  utilization?: number | null
  parts?: { system: number; tools: number; messages: number } | null
}): UsageReport {
  return {
    context: {
      tokens: overrides.tokens ?? null,
      window: overrides.window ?? null,
      utilization: overrides.utilization ?? null,
      parts: overrides.parts ?? null,
    },
    cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
  }
}

describe('compactUsage', () => {
  it('没有读数时显示「—」，不是 0', () => {
    expect(compactUsage(null)).toBe('—')
    expect(compactUsage(usageOf({}))).toBe('—')
  })

  it('两个数都在时是 12.3k tok · 9%', () => {
    expect(compactUsage(usageOf({ tokens: 12_345, window: 128_000, utilization: 0.0925 }))).toBe(
      '12.3k tok · 9%',
    )
  })

  it('只有 tokens（问不到模型窗口）时不给假的百分比', () => {
    const text = compactUsage(usageOf({ tokens: 12_345 }))

    expect(text).toBe('12.3k tok')
    expect(text).not.toContain('0%')
  })

  it('不足一千不缩写成 k', () => {
    expect(compactUsage(usageOf({ tokens: 980 }))).toBe('980 tok')
  })

  it('窗口整数倍不拖小数点', () => {
    expect(compactUsage(usageOf({ tokens: 128_000, utilization: 1 }))).toBe('128k tok · 100%')
  })

  it('确实为 0 就显示 0，不被当成缺数', () => {
    expect(compactUsage(usageOf({ tokens: 0, window: 128_000, utilization: 0 }))).toBe(
      '0 tok · 0%',
    )
  })
})

describe('contextSegments', () => {
  it('usage 为 null → null（不画条）', () => {
    expect(contextSegments(null)).toBeNull()
  })

  it('parts 为 null → null（不画条）', () => {
    expect(contextSegments(usageOf({ tokens: 1000, parts: null }))).toBeNull()
  })

  it('三块全 0 → null（空条与没数据同形，不如不画）', () => {
    expect(contextSegments(usageOf({ parts: { system: 0, tools: 0, messages: 0 } }))).toBeNull()
  })

  it('三段顺序固定且占比和为 1', () => {
    const segments = contextSegments(
      usageOf({ tokens: 1000, parts: { system: 200, tools: 300, messages: 500 } }),
    )

    expect(segments?.map((segment) => segment.key)).toEqual(['system', 'tools', 'messages'])
    expect(segments?.map((segment) => segment.ratio)).toEqual([0.2, 0.3, 0.5])
  })
})
