// @vitest-environment jsdom
/**
 * PermissionSelector 的用例。
 *
 * 这里守的是"**沙箱状态按事实显示**"这条纪律：`capabilities.sandbox.network` 是后端实测能否
 * 强制网络隔离的结论（`policy/sandbox.probe_backend()`），不是"当前档位允不允许出网"。
 * 按模式名反推会给用户一个查不出来的错答案，所以把它钉在用例里：
 * network=true → 出网可被限制；available=false → 必须给出失败原因。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SandboxState } from '../../../../api/types'
import { PermissionSelector } from '../PermissionSelector'

afterEach(cleanup)

function sandboxOf(overrides: Partial<SandboxState> = {}): SandboxState {
  return {
    backend: 'bwrap',
    available: true,
    network: true,
    reason: null,
    landlock_abi: 4,
    ...overrides,
  }
}

function open(sandbox: SandboxState | null): void {
  render(<PermissionSelector value="manual" onChange={vi.fn()} sandbox={sandbox} />)
  fireEvent.click(screen.getByRole('button', { name: '手动' }))
}

describe('PermissionSelector', () => {
  it('三档都在，当前档 aria-selected', () => {
    open(null)

    expect(screen.getAllByRole('option')).toHaveLength(3)
    expect(screen.getByRole('option', { name: /手动/ }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('option', { name: /完全访问/ }).getAttribute('aria-selected')).toBe(
      'false',
    )
  })

  it('点某一档带上该档的模式；点当前档不重复通知', () => {
    const onChange = vi.fn()
    render(<PermissionSelector value="manual" onChange={onChange} sandbox={null} />)

    fireEvent.click(screen.getByRole('button', { name: '手动' }))
    fireEvent.click(screen.getByRole('option', { name: /自动/ }))
    expect(onChange).toHaveBeenCalledWith('auto')

    fireEvent.click(screen.getByRole('button', { name: '手动' }))
    fireEvent.click(screen.getByRole('option', { name: /手动/ }))
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('沙箱不可用时说清事实并给出原因，而不是照模式反推', () => {
    open(
      sandboxOf({
        backend: 'none',
        available: false,
        network: false,
        reason: '找不到 bubblewrap（bwrap）',
        landlock_abi: null,
      }),
    )

    expect(screen.getByText('沙箱不可用')).toBeTruthy()
    expect(screen.getByRole('tooltip').textContent).toBe('找不到 bubblewrap（bwrap）')
    expect(screen.getByText(/无法限制出网/)).toBeTruthy()
  })

  it('沙箱可用且能强制网络隔离时说"出网可被限制"', () => {
    open(sandboxOf({ network: true }))

    expect(screen.getByText(/沙箱可用/)).toBeTruthy()
    expect(screen.getByText(/出网可被限制/)).toBeTruthy()
  })

  it('沙箱可用但不能强制网络隔离时不为它遮丑', () => {
    open(sandboxOf({ network: false, landlock_abi: null }))

    expect(screen.getByText(/无法限制出网/)).toBeTruthy()
  })

  it('disabled 时下拉打不开', () => {
    render(
      <PermissionSelector value="manual" onChange={vi.fn()} sandbox={null} disabled />,
    )

    fireEvent.click(screen.getByRole('button', { name: '手动' }))

    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('没有 meta 时不编造沙箱状态', () => {
    open(null)

    // 沙箱状态整行（含它的 Tooltip）都不渲染：`full` 档的 caveat 里也会出现"沙箱"两个字，
    // 所以按文本查会误判——按 Tooltip 这个结构标记查才准。
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
