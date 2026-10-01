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

  it('插槽缺省时只有区域标注，不含任何真实构件', () => {
    const { container } = render(<AppShell />)

    // 默认态没有任何交互构件（按钮/输入/图标）——真实内容全部由表面插槽注入
    expect(container.querySelectorAll('button, input, svg')).toHaveLength(0)
    expect(screen.getByText(/侧栏 240px/)).toBeTruthy()
    expect(screen.getByText(/右栏 280px/)).toBeTruthy()
  })
})
