// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { UsageReport } from '../../../api/types'
import { ContextRing } from '../ContextRing'

afterEach(cleanup)

function report(overrides: Partial<UsageReport> = {}): UsageReport {
  return {
    context: {
      tokens: 55_700,
      window: 100_000,
      utilization: 0.557,
      parts: { system: 1_100, tools: 2_000, messages: 52_600 },
    },
    cache: { read_tokens: 9_000, write_tokens: 1_000, hit_ratio: 0.976 },
    compaction: { count: 0, last_compaction_tokens: null, last_step: null },
    ...overrides,
  }
}

/** First dasharray segment = arc length, used to assert how full the ring is. */
function arc(container: HTMLElement): number {
  const circles = container.querySelectorAll('circle')
  const dash = circles[1]?.getAttribute('stroke-dasharray') ?? '0 0'
  return Number.parseFloat(dash)
}

describe('上下文容量环', () => {
  it('环的弧长跟着占用率走（读数变了环就变）', () => {
    const { container } = render(<ContextRing usage={report()} />)
    const half = arc(container)

    cleanup()
    const { container: full } = render(
      <ContextRing usage={report({ context: { tokens: 95_000, window: 100_000, utilization: 0.95, parts: null } })} />,
    )
    expect(arc(full)).toBeGreaterThan(half)
  })

  it('没有读数时环是空轨，标签给「—」而不是 0', () => {
    const { container } = render(<ContextRing usage={null} />)

    expect(arc(container)).toBe(0)
    expect(screen.getByRole('button', { name: '上下文容量 —' })).toBeTruthy()
  })

  it('点开是明细：容量（用/总 + 占用率）、分块构成、平均缓存命中率', () => {
    render(<ContextRing usage={report()} />)
    fireEvent.click(screen.getByRole('button', { name: '上下文容量 55.7%' }))

    const dialog = screen.getByRole('dialog', { name: '上下文容量' })
    expect(dialog.textContent).toContain('5.6万/10万（55.7%）')
    expect(dialog.textContent).toContain('消息')
    expect(dialog.textContent).toContain('94.4%') // 52,600 / 55,700
    expect(dialog.textContent).toContain('系统工具')
    expect(dialog.textContent).toContain('3.6%') // 2,000 / 55,700
    expect(dialog.textContent).toContain('平均缓存命中率')
    expect(dialog.textContent).toContain('97.6%')
  })

  it('分块没读数时那三行给「—」（不编数字）', () => {
    render(<ContextRing usage={report({ context: { tokens: 1_000, window: 100_000, utilization: 0.01, parts: null } })} />)
    fireEvent.click(screen.getByRole('button', { name: '上下文容量 1.0%' }))

    expect(screen.getByRole('dialog').textContent).toContain('—')
  })

  it('占用率越过警戒线换成 danger 色（环与容量条一起）', () => {
    const calm = render(<ContextRing usage={report()} />)
    expect(calm.container.querySelector('button')?.className).not.toContain('text-danger')
    calm.unmount()

    const hot = render(
      <ContextRing usage={report({ context: { tokens: 92_000, window: 100_000, utilization: 0.92, parts: null } })} />,
    )
    expect(hot.container.querySelector('button')?.className).toContain('text-danger')
    fireEvent.click(screen.getByRole('button', { name: '上下文容量 92.0%' }))
    expect(hot.container.querySelector('div[style*="width"]')?.className).toContain('bg-danger')
  })
})
