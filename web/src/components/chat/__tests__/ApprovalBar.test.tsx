// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LiveApproval } from '../../../state/useRunStream'
import { ApprovalBar } from '../ApprovalBar'

const APPROVAL: LiveApproval = {
  approvalId: 'a1',
  tool: 'bash',
  arguments: '{"command": "rm -rf /"}',
  reason: '递归删除根目录',
}

afterEach(cleanup)

describe('ApprovalBar（审批条，参考报告 hana-rise）', () => {
  it('无待决审批时不渲染', () => {
    const { container } = render(<ApprovalBar approvals={[]} busy={false} onDecide={() => {}} />)
    expect(container.innerHTML).toBe('')
  })

  it('渲染工具名、理由与参数摘要', () => {
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={() => {}} />)

    expect(screen.getByText('请求执行：bash')).toBeTruthy()
    expect(screen.getByText('递归删除根目录')).toBeTruthy()
    expect(screen.getByText(/rm -rf \//)).toBeTruthy()
  })

  it('拒绝一步到位：立即回调 deny', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} />)

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'deny')
  })

  it('两步确认：第一次点「允许」不回调，第二张卡确认才回调 allow', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} />)

    fireEvent.click(screen.getByRole('button', { name: '允许' }))
    expect(onDecide).not.toHaveBeenCalled()
    // 原地出第二张「确认执行」卡，第一次的命令仍在眼前
    expect(screen.getByText('递归删除根目录')).toBeTruthy()
    expect(screen.getByText(/毁灭级命令：确认后立即执行/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '确认执行' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'allow')
  })

  it('第二张卡可取消：回到第一步且不回调', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} />)

    fireEvent.click(screen.getByRole('button', { name: '允许' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onDecide).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(screen.getByRole('button', { name: '允许' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '拒绝' })).toBeTruthy()
  })

  it('busy 时按钮禁用（等待后端幂等确认）', () => {
    render(<ApprovalBar approvals={[APPROVAL]} busy onDecide={() => {}} />)

    expect((screen.getByRole('button', { name: '允许' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '拒绝' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('多条待决审批纵向堆叠', () => {
    render(
      <ApprovalBar
        approvals={[APPROVAL, { ...APPROVAL, approvalId: 'a2', tool: 'write_file' }]}
        busy={false}
        onDecide={() => {}}
      />,
    )

    expect(screen.getByText('请求执行：bash')).toBeTruthy()
    expect(screen.getByText('请求执行：write_file')).toBeTruthy()
  })
})
