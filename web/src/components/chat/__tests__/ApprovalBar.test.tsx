// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LiveApproval } from '../../../state/useRunStream'
import { ApprovalBar } from '../ApprovalBar'

const APPROVAL: LiveApproval = {
  approvalId: 'a1',
  tool: 'bash',
  arguments: '{"command": "rm -rf /tmp/x"}',
  reason: '目标在工作区之外',
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
    expect(screen.getByText('目标在工作区之外')).toBeTruthy()
    expect(screen.getByText(/rm -rf \/tmp\/x/)).toBeTruthy()
  })

  it('允许 / 拒绝各带决策回调', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} />)

    fireEvent.click(screen.getByRole('button', { name: '允许' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'allow')
    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'deny')
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
