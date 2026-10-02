// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SidebarFooter } from '../SidebarFooter'

/**
 * 侧栏底栏（设置入口）：它必须是**自己一栏**（独立的顶发丝线与留白、贴住栏底），
 * 不是会话列表里的又一项——所以这里既验交互，也验「单开一栏」这件事本身。
 */
describe('侧栏底栏（设置入口）', () => {
  afterEach(cleanup)

  it('是一个带文字的按钮：图标 + 「设置」，键盘可达', () => {
    render(<SidebarFooter onOpenSettings={() => {}} />)

    const button = screen.getByRole('button', { name: '设置' })
    expect(button.getAttribute('type')).toBe('button')
    expect(button.textContent).toContain('设置')
    expect(button.querySelector('svg')).toBeTruthy()
  })

  it('点击回调设置入口（侧栏底栏只负责触发，不自己开关面板）', () => {
    const onOpenSettings = vi.fn()
    render(<SidebarFooter onOpenSettings={onOpenSettings} />)

    fireEvent.click(screen.getByRole('button', { name: '设置' }))

    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it('单开一栏：自带上边线与贴底留白，不吃会话列表的 hover 态', () => {
    const { container } = render(<SidebarFooter onOpenSettings={() => {}} />)
    const bar = container.firstElementChild as HTMLElement

    expect(bar.className).toContain('border-t')
    expect(bar.className).toContain('mt-auto')
    // 栏本身不是可点区域（只有里面那个按钮是），否则整条空白都会亮起来
    expect(bar.getAttribute('role')).toBeNull()
  })
})
