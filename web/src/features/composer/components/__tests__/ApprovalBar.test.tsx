// @vitest-environment jsdom
/**
 * ApprovalBar 的用例。
 *
 * 除了"空数组不渲染"与"点哪个按钮回什么参数"，这里刻意钉住两条安全相关的行为：
 *   · 倒计时只在调用方给了 `now` 时才出现（组件不读 Date.now，否则对时间不可测）；
 *   · `busy` 时两个按钮一起禁用——服务端对重复投递只回 accepted:false，
 *     界面若还能点第二下，用户会以为第一次没生效。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Approval } from '../../../../api/types'
import { ApprovalBar } from '../ApprovalBar'

afterEach(cleanup)

function approvalOf(overrides: Partial<Approval> = {}): Approval {
  return {
    approval_id: 'ap-1',
    tool: 'shell',
    arguments: { command: 'rm -rf build' },
    reason: '删除目录需要确认',
    created_at: 1_000,
    expires_at: 43_000,
    decision: null,
    resolved_at: null,
    resolved_reason: null,
    ...overrides,
  }
}

describe('ApprovalBar', () => {
  it('没有待审批时什么都不渲染', () => {
    const { container } = render(<ApprovalBar approvals={[]} onAnswer={vi.fn()} />)

    expect(container.firstChild).toBeNull()
    expect(screen.queryByLabelText('待审批')).toBeNull()
  })

  it('一条审批：工具名、原因与参数摘要都在', () => {
    render(<ApprovalBar approvals={[approvalOf()]} onAnswer={vi.fn()} />)

    expect(screen.getByLabelText('待审批')).toBeTruthy()
    expect(screen.getByText('shell')).toBeTruthy()
    expect(screen.getByText('删除目录需要确认')).toBeTruthy()
    expect(screen.getByText(/"command": "rm -rf build"/)).toBeTruthy()
  })

  it('给 allow 回 (approvalId, "allow")', () => {
    const onAnswer = vi.fn()
    render(<ApprovalBar approvals={[approvalOf({ approval_id: 'ap-9' })]} onAnswer={onAnswer} />)

    fireEvent.click(screen.getByRole('button', { name: '允许' }))

    expect(onAnswer).toHaveBeenCalledWith('ap-9', 'allow')
  })

  it('给 deny 回 (approvalId, "deny")', () => {
    const onAnswer = vi.fn()
    render(<ApprovalBar approvals={[approvalOf({ approval_id: 'ap-9' })]} onAnswer={onAnswer} />)

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))

    expect(onAnswer).toHaveBeenCalledWith('ap-9', 'deny')
  })

  it('多条时逐条一卡，各自带自己的 approval_id', () => {
    const onAnswer = vi.fn()
    render(
      <ApprovalBar
        approvals={[
          approvalOf({ approval_id: 'ap-1', tool: 'shell' }),
          approvalOf({ approval_id: 'ap-2', tool: 'files' }),
        ]}
        onAnswer={onAnswer}
      />,
    )

    expect(screen.getAllByRole('button', { name: '允许' })).toHaveLength(2)
    // 第二条卡的拒绝按钮：按名字查不到区别，所以按文档顺序取第二个。
    fireEvent.click(screen.getAllByRole('button', { name: '拒绝' })[1] as HTMLElement)

    expect(onAnswer).toHaveBeenCalledWith('ap-2', 'deny')
  })

  it('busy 时两个按钮都禁用', () => {
    render(<ApprovalBar approvals={[approvalOf()]} onAnswer={vi.fn()} busy />)

    expect((screen.getByRole('button', { name: '允许' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '拒绝' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('给了 now 才显示剩余秒数', () => {
    const { rerender } = render(
      <ApprovalBar approvals={[approvalOf({ expires_at: 43_000 })]} onAnswer={vi.fn()} now={1_000} />,
    )
    expect(screen.getByText('剩余 42 秒')).toBeTruthy()

    // now 缺省 → 不显示倒计时（组件不自己读 Date.now）。
    rerender(<ApprovalBar approvals={[approvalOf({ expires_at: 43_000 })]} onAnswer={vi.fn()} />)
    expect(screen.queryByText(/剩余/)).toBeNull()
  })

  it('已经过期说明已过期，而不是负秒数', () => {
    render(
      <ApprovalBar approvals={[approvalOf({ expires_at: 1_000 })]} onAnswer={vi.fn()} now={9_000} />,
    )

    expect(screen.getByText('已过期')).toBeTruthy()
    expect(screen.queryByText(/剩余/)).toBeNull()
  })
})
