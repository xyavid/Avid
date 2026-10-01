// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PermissionButton } from '../PermissionButton'

function openCard() {
  fireEvent.click(screen.getByRole('button', { name: /权限模式/ }))
}

afterEach(cleanup)

describe('PermissionButton（输入区权限按钮）', () => {
  it('chip 反映当前态势：图标与文案随模式变化', () => {
    const { container, rerender } = render(<PermissionButton mode="manual" onChange={() => {}} />)

    expect(screen.getByText('手动')).toBeTruthy()
    expect(container.querySelector('svg')).toBeTruthy()
    expect(container.querySelector('svg')?.getAttribute('stroke')).toBe('currentColor')

    rerender(<PermissionButton mode="full" onChange={() => {}} />)
    expect(screen.getByText('完全')).toBeTruthy()
    // full 是关闭边界的危险态，chip 用 danger 色
    expect(screen.getByRole('button', { name: /权限模式/ }).className).toContain('text-danger')
  })

  it('点击出卡片：三个模式各带自己的图标，当前项有勾', () => {
    render(<PermissionButton mode="auto" onChange={() => {}} />)
    openCard()

    const dialog = screen.getByRole('dialog', { name: '权限模式' })
    expect(dialog.textContent).toContain('手动')
    expect(dialog.textContent).toContain('自动')
    expect(dialog.textContent).toContain('完全')
    // 三个选项图标 + 当前项（auto）的勾 = 4
    expect(dialog.querySelectorAll('svg').length).toBe(4)
  })

  it('选 auto：回调触发且卡片关闭', () => {
    const onChange = vi.fn()
    render(<PermissionButton mode="manual" onChange={onChange} />)
    openCard()

    fireEvent.click(screen.getByRole('button', { name: /自动/ }))
    expect(onChange).toHaveBeenCalledWith('auto')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('full 两段确认：第一次只出确认块，确认后才回调', () => {
    const onChange = vi.fn()
    render(<PermissionButton mode="manual" onChange={onChange} />)
    openCard()

    fireEvent.click(screen.getByRole('button', { name: /完全/ }))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText(/意味着工具可以无限制/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '确认开启' }))
    expect(onChange).toHaveBeenCalledWith('full')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('点击遮罩关闭', () => {
    render(<PermissionButton mode="manual" onChange={() => {}} />)
    openCard()

    fireEvent.click(document.querySelector('[data-testid="popover-backdrop"]')!)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
