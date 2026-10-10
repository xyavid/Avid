// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PermissionButton } from '../PermissionButton'

function openCard() {
  fireEvent.click(screen.getByRole('button', { name: /权限/ }))
}

afterEach(cleanup)

describe('PermissionButton（输入区权限按钮）', () => {
  it('chip 反映当前态势：默认（normal）与完全访问两态', () => {
    const { container, rerender } = render(<PermissionButton full={false} onToggleFull={() => {}} />)

    expect(screen.getByText('默认')).toBeTruthy()
    expect(container.querySelector('svg')).toBeTruthy()
    expect(container.querySelector('svg')?.getAttribute('stroke')).toBe('currentColor')
    expect(screen.getByRole('button', { name: /权限/ }).className).not.toContain('text-danger')

    rerender(<PermissionButton full onToggleFull={() => {}} />)
    expect(screen.getByText('完全访问')).toBeTruthy()
    // Full access is the dangerous state of the boundary toggle, so the chip turns danger.
    expect(screen.getByRole('button', { name: /权限/ }).className).toContain('text-danger')
  })

  it('点击出卡片：默认 / 完全访问两项各带自己的图标，当前项有勾', () => {
    render(<PermissionButton full={false} onToggleFull={() => {}} />)
    openCard()

    const dialog = screen.getByRole('dialog', { name: '权限' })
    expect(within(dialog).getByText('默认')).toBeTruthy()
    expect(within(dialog).getByText('完全访问')).toBeTruthy()
    // The default option spells out the cost: destructive commands ask once.
    expect(within(dialog).getByText('毁灭级命令会问你一次')).toBeTruthy()
    // Two option icons plus the check on the current (default) option = 3.
    expect(dialog.querySelectorAll('svg').length).toBe(3)
  })

  it('从完全访问降回默认：不设确认，直接回调且卡片关闭', () => {
    const onToggleFull = vi.fn()
    render(<PermissionButton full onToggleFull={onToggleFull} />)
    openCard()

    fireEvent.click(screen.getByRole('button', { name: /默认/ }))
    expect(onToggleFull).toHaveBeenCalledWith(false)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('完全访问两段确认：第一次只出确认块，确认后才回调；取消回到第一步', () => {
    const onToggleFull = vi.fn()
    render(<PermissionButton full={false} onToggleFull={onToggleFull} />)
    openCard()

    fireEvent.click(screen.getByRole('button', { name: /完全访问/ }))
    expect(onToggleFull).not.toHaveBeenCalled()
    expect(screen.getByText(/跳过毁灭级确认/)).toBeTruthy()

    // Cancel retracts the confirm block, stays on step one, and fires nothing.
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByText(/跳过毁灭级确认/)).toBeNull()
    expect(screen.getByRole('dialog', { name: '权限' })).toBeTruthy()
    expect(onToggleFull).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /完全访问/ }))
    fireEvent.click(screen.getByRole('button', { name: '确认开启' }))
    expect(onToggleFull).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('点击遮罩关闭', () => {
    render(<PermissionButton full={false} onToggleFull={() => {}} />)
    openCard()

    fireEvent.click(document.querySelector('[data-testid="popover-backdrop"]')!)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('compact：文字收成不可见，可访问名与提示仍是「默认权限」', () => {
    const { rerender } = render(<PermissionButton full={false} onToggleFull={() => {}} />)
    expect(screen.getByText('默认').className).not.toContain('sr-only')

    rerender(<PermissionButton full={false} onToggleFull={() => {}} compact />)

    expect(screen.getByText('默认').className).toContain('sr-only')
    const chip = screen.getByRole('button', { name: /权限/ })
    expect(chip.textContent).toContain('默认')
    expect(chip.getAttribute('title')).toBe('默认权限')
  })

  it('没有常驻边框：框只在悬停 / 键盘聚焦 / 展开时出现', () => {
    const { rerender } = render(<PermissionButton full={false} onToggleFull={() => {}} />)
    const chip = () => screen.getByRole('button', { name: /权限/ })
    const classes = () => chip().className.split(' ')

    expect(classes()).not.toContain('border-hairline')
    expect(classes()).not.toContain('border-hair')
    expect(classes()).toContain('hover:bg-overlay-light')
    expect(classes()).toContain('focus-visible:bg-overlay-light')

    // Open shows the same box as hover, or the click would land on nothing.
    fireEvent.click(chip())
    expect(classes()).toContain('bg-overlay-light')
    fireEvent.click(chip())

    // Full access keeps its danger tint at rest and on hover — the box is never neutral grey there.
    rerender(<PermissionButton full onToggleFull={() => {}} />)
    expect(classes()).not.toContain('border-hairline')
    expect(classes()).toContain('bg-danger/[0.08]')
    expect(classes()).toContain('hover:bg-danger/[0.14]')
  })
})
