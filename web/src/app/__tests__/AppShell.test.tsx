// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { AppShell } from '../AppShell'

describe('AppShell 骨架（阶段 1）', () => {
  afterEach(cleanup)

  it('渲染顶栏字标与三栏区域的验收标注', () => {
    render(<AppShell />)

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText(/侧栏 240px/)).toBeTruthy()
    expect(screen.getByText(/对话列 ≤720px/)).toBeTruthy()
    expect(screen.getByText(/右栏 280px/)).toBeTruthy()
  })

  it('顶栏是「标记 + 字标」的锁定组合（阶段 7 起，标记不再只是字标）', () => {
    const { container } = render(<AppShell />)

    const header = container.querySelector('header')
    const mark = header?.querySelector('svg')
    expect(mark).toBeTruthy()
    expect(mark?.getAttribute('viewBox')).toBe('0 0 48 48')
    expect(mark?.querySelectorAll('path')).toHaveLength(1)
    expect(header?.textContent).toContain('Avid')
  })

  it('插槽缺省时只有区域标注，不含任何真实构件', () => {
    const { container } = render(<AppShell />)

    // 默认态没有任何**交互**构件（按钮/输入）——真实内容全部由表面插槽注入。
    // svg 只剩顶栏那一枚标识：它是字标的一部分，不是可交互图标。
    expect(container.querySelectorAll('button, input')).toHaveLength(0)
    expect(container.querySelectorAll('svg')).toHaveLength(1)
    expect(screen.getByText(/侧栏 240px/)).toBeTruthy()
    expect(screen.getByText(/右栏 280px/)).toBeTruthy()
  })
})
