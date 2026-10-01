import { describe, expect, it } from 'vitest'

import { formatCache, formatUsage } from '../usage'
import type { UsageReport } from '../../../../api/types'

interface ReportInput {
  tokens?: number | null
  window?: number | null
  utilization?: number | null
  read?: number | null
  write?: number | null
  hit?: number | null
}

/** 造一份 usage 快照；`?? null` 只在 undefined 时兜底，所以传 0 仍然是 0。 */
function report(input: ReportInput): UsageReport {
  return {
    context: {
      tokens: input.tokens ?? null,
      window: input.window ?? null,
      utilization: input.utilization ?? null,
      parts: null,
    },
    cache: {
      read_tokens: input.read ?? null,
      write_tokens: input.write ?? null,
      hit_ratio: input.hit ?? null,
    },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
  }
}

describe('formatUsage', () => {
  it('没有快照时显示「—」', () => {
    expect(formatUsage(null)).toBe('—')
  })

  it('tokens 或 window 为 null 时显示「—」，不当 0', () => {
    expect(formatUsage(report({ tokens: null, window: 128_000 }))).toBe('—')
    expect(formatUsage(report({ tokens: 12_300, window: null }))).toBe('—')
    // window 为 0 算不出占用率，同样按"没有这个数"处理。
    expect(formatUsage(report({ tokens: 12_300, window: 0 }))).toBe('—')
  })

  it('确认的 0 与 null 是两回事：0 照常渲染', () => {
    // 这条是「null ≠ 0」的核心用例：同样是"什么都没有"，0 是一条事实，null 是没有数据。
    expect(formatUsage(report({ tokens: 0, window: 128_000, utilization: 0 }))).toBe(
      '0 / 128k · 0%',
    )
  })

  it('按 token 数 + 窗口 + 占用率给出读数', () => {
    expect(
      formatUsage(report({ tokens: 12_300, window: 128_000, utilization: 0.096 })),
    ).toBe('12.3k / 128k · 9%')
  })

  it('utilization 缺失时用两个已有的数算，不因此丢掉整个读数', () => {
    expect(formatUsage(report({ tokens: 64_000, window: 128_000, utilization: null }))).toBe(
      '64k / 128k · 50%',
    )
  })

  it('四位数以下不给小数位', () => {
    expect(formatUsage(report({ tokens: 950, window: 4_000, utilization: 0.2375 }))).toBe(
      '950 / 4k · 23%',
    )
  })
})

describe('formatCache', () => {
  it('没有快照时不出现', () => {
    expect(formatCache(null)).toBeNull()
  })

  it('hit_ratio 为 null 时不出现（不是 0%）', () => {
    expect(formatCache(report({ hit: null, write: 120 }))).toBeNull()
    expect(formatCache(report({ hit: 0.62, write: null }))).toBeNull()
  })

  it('有写入计数时给出命中率', () => {
    expect(formatCache(report({ hit: 0.62, write: 120 }))).toBe('命中 62%')
  })

  it('确认的未命中是「命中 0%」，不是不出现', () => {
    // 0 是真实计数（这一家确实上报了缓存计数），不能因为它是 0 就被当成"没有数据"。
    expect(formatCache(report({ hit: 0, write: 0 }))).toBe('命中 0%')
  })
})
