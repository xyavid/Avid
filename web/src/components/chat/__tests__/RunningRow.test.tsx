// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { RunningRow } from '../RunningRow'

afterEach(cleanup)

describe('RunningRow（输入区上方的运行中指示）', () => {
  it('三个点、跑的是气泡里那支 hana-cycling-dots，并且有可读的状态名', () => {
    const { container } = render(<RunningRow />)

    const row = screen.getByRole('status', { name: '运行中' })
    const dots = [...row.querySelectorAll('span')]
    expect(dots).toHaveLength(3)
    for (const [index, dot] of dots.entries()) {
      expect(dot.getAttribute('style')).toContain('hana-cycling-dots 1.2s')
      expect(dot.getAttribute('style')).toContain(`animation-delay: ${index * 0.2}s`)
    }
    expect(container.querySelector('[data-testid="run-indicator"]')).toBe(row)
  })
})
