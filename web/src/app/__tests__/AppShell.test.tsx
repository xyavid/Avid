// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AppShell } from '../AppShell'

describe('AppShell 骨架（阶段 1 立，阶段 48 支持无 rail 两列）', () => {
  afterEach(cleanup)

  it('渲染顶栏字标与侧栏、对话列的验收标注', () => {
    render(<AppShell />)

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText(/侧栏 240px/)).toBeTruthy()
    expect(screen.getByText(/对话列 ≤720px/)).toBeTruthy()
  })

  it('顶栏是「标记 + 字标」的锁定组合（标记是图，不是内联 SVG）', () => {
    const { container } = render(<AppShell />)

    const header = container.querySelector('header')
    const mark = header?.querySelector('img')
    expect(mark).toBeTruthy()
    expect(mark?.getAttribute('src') ?? '').toContain('avid-mark')
    expect(mark?.getAttribute('aria-hidden')).toBe('true')
    expect(header?.textContent).toContain('Avid')
  })

  it('插槽缺省时只有区域标注，不含任何真实构件', () => {
    const { container } = render(<AppShell />)

    // Default state renders no interactive widgets; surfaces inject all real content via slots.
    expect(container.querySelectorAll('button, input, svg')).toHaveLength(0)
    expect(container.querySelectorAll('img')).toHaveLength(1)
    expect(screen.getByText(/侧栏 240px/)).toBeTruthy()
  })

  it('不给 rail：两列模板（侧栏 + 主列），没有右列', () => {
    const { container } = render(<AppShell />)

    const grid = container.querySelector('div[style*="grid-template-columns"]') as HTMLElement
    expect(grid.style.gridTemplateColumns).toMatch(/^\d+px minmax\(0,1fr\)$/)
    expect(screen.queryByText(/右栏 280px/)).toBeNull()
  })

  it('给 rail：三列模板，右栏内容进第三列', () => {
    const { container } = render(<AppShell rail={<p>右栏内容</p>} />)

    const grid = container.querySelector('div[style*="grid-template-columns"]') as HTMLElement
    expect(grid.style.gridTemplateColumns).toMatch(/^\d+px minmax\(0,1fr\) \d+px$/)
    expect(screen.getByText('右栏内容')).toBeTruthy()
  })
})

describe('两侧列可拖（阶段 52）', () => {
  afterEach(cleanup)

  const grid = (container: HTMLElement) =>
    container.querySelector('div[style*="grid-template-columns"]') as HTMLElement

  it('两条列缘各有一个拖拽柄；没有右列时只有一条', () => {
    const both = render(<AppShell rail={<p>右栏</p>} />)
    expect(screen.getByRole('separator', { name: '调整侧栏宽度' })).toBeTruthy()
    expect(screen.getByRole('separator', { name: '调整侧边栏宽度' })).toBeTruthy()
    both.unmount()

    render(<AppShell />)
    expect(screen.getAllByRole('separator')).toHaveLength(1)
  })

  it('键盘可调：方向键改模板并落盘（方向按「变宽」算）', () => {
    const { container } = render(<AppShell rail={<p>右栏</p>} />)
    const before = grid(container).style.gridTemplateColumns

    fireEvent.keyDown(screen.getByRole('separator', { name: '调整侧栏宽度' }), { key: 'ArrowRight' })
    const wider = grid(container).style.gridTemplateColumns
    expect(wider).not.toBe(before)
    expect(Number.parseInt(wider, 10)).toBeGreaterThan(Number.parseInt(before, 10))

    // Sidebar arrow keys: ArrowLeft narrows and clamps at the lower bound.
    for (let i = 0; i < 20; i += 1) {
      fireEvent.keyDown(screen.getByRole('separator', { name: '调整侧栏宽度' }), { key: 'ArrowLeft' })
    }
    const floored = grid(container).style.gridTemplateColumns
    expect(Number.parseInt(floored, 10)).toBe(180)

    // Rail grows with ArrowLeft.
    fireEvent.keyDown(screen.getByRole('separator', { name: '调整侧边栏宽度' }), { key: 'ArrowLeft' })
    expect(JSON.parse(localStorage.getItem('avid.columns') ?? '{}').rail).toBeGreaterThan(280)
  })
})
