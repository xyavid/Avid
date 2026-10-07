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
    // 完全访问是关闭边界的危险态，chip 用 danger 色
    expect(screen.getByRole('button', { name: /权限/ }).className).toContain('text-danger')
  })

  it('点击出卡片：默认 / 完全访问两项各带自己的图标，当前项有勾', () => {
    render(<PermissionButton full={false} onToggleFull={() => {}} />)
    openCard()

    const dialog = screen.getByRole('dialog', { name: '权限' })
    expect(within(dialog).getByText('默认')).toBeTruthy()
    expect(within(dialog).getByText('完全访问')).toBeTruthy()
    // 默认项说清代价：毁灭级命令会问你一次
    expect(within(dialog).getByText('毁灭级命令会问你一次')).toBeTruthy()
    // 两个选项图标 + 当前项（默认）的勾 = 3
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

    // 取消：确认块收回，仍停在第一步，没有回调
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
})
