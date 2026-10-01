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

  it('骨架不含任何真实内容：只有占位框与标注', () => {
    const { container } = render(<AppShell />)

    // 占位搜索框是骨架里唯一的"构件"，它只是个 30px 的空框
    const boxes = container.querySelectorAll('div[aria-hidden]')
    expect(boxes.length).toBe(1)
    expect(boxes[0]?.textContent).toBe('')
  })
})
