// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { UsageReport } from '../../../api/types'
import { ContextRail } from '../ContextRail'

const USAGE: UsageReport = {
  context: { tokens: 5200, window: 400000, utilization: 0.013, parts: null },
  cache: { read_tokens: null, write_tokens: null, hit_ratio: 0.808 },
  compaction: { count: 0, last_compaction_tokens: null, last_step: null },
}

describe('ContextRail（右栏 · 上下文卡）', () => {
  afterEach(cleanup)

  it('只放上下文读数：窗口/已用/占用率/缓存命中', () => {
    render(<ContextRail usage={USAGE} />)

    expect(screen.getByText('已用 tokens').nextElementSibling?.textContent).toBe('5,200')
    expect(screen.getByText('上下文窗口').nextElementSibling?.textContent).toBe('400,000')
    expect(screen.getByText('占用率').nextElementSibling?.textContent).toBe('1.3%')
    expect(screen.getByText('缓存命中率').nextElementSibling?.textContent).toBe('80.8%')
  })

  it('不放模型/工具/技能/沙箱/工作区——它们不属于这张卡', () => {
    render(<ContextRail usage={USAGE} />)

    expect(screen.queryByText('模型')).toBeNull()
    expect(screen.queryByText('工具')).toBeNull()
    expect(screen.queryByText('技能')).toBeNull()
    expect(screen.queryByText('沙箱')).toBeNull()
    expect(screen.queryByText('工作区')).toBeNull()
  })

  it('没有用量快照时显示 —（未上报 ≠ 0）', () => {
    render(<ContextRail usage={null} />)

    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(4)
  })
})
