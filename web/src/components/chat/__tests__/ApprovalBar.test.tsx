// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { LiveApproval } from '../../../state/useRunStream'
import { ApprovalBar } from '../ApprovalBar'

const APPROVAL: LiveApproval = {
  approvalId: 'a1',
  kind: 'approval',
  tool: 'bash',
  arguments: '{"command": "rm -rf /"}',
  reason: '递归删除根目录',
  options: [],
}

const QUESTION: LiveApproval = {
  approvalId: 'q1',
  kind: 'question',
  tool: 'ask_user',
  arguments: '{"question": "用哪个名字？", "options": ["甲", "乙"]}',
  reason: '用哪个名字？',
  options: ['甲', '乙'],
}

const noop = () => {}

afterEach(cleanup)

describe('ApprovalBar（审批条，参考报告 hana-rise）', () => {
  it('无待决审批时不渲染', () => {
    const { container } = render(
      <ApprovalBar approvals={[]} busy={false} onDecide={noop} onAnswer={noop} />,
    )
    expect(container.innerHTML).toBe('')
  })

  it('渲染工具名、理由与参数摘要', () => {
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={noop} onAnswer={noop} />)

    expect(screen.getByText('请求执行：bash')).toBeTruthy()
    expect(screen.getByText('递归删除根目录')).toBeTruthy()
    expect(screen.getByText(/rm -rf \//)).toBeTruthy()
  })

  it('拒绝一步到位：立即回调 deny', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} onAnswer={noop} />)

    fireEvent.click(screen.getByRole('button', { name: '拒绝' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'deny')
  })

  it('两步确认：第一次点「允许」不回调，第二张卡确认才回调 allow', () => {
    const onDecide = vi.fn()
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} onAnswer={noop} />)

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
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={onDecide} onAnswer={noop} />)

    fireEvent.click(screen.getByRole('button', { name: '允许' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    expect(onDecide).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认执行' })).toBeNull()
    expect(screen.getByRole('button', { name: '允许' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '拒绝' })).toBeTruthy()
  })

  it('busy 时按钮禁用（等待后端幂等确认）', () => {
    render(<ApprovalBar approvals={[APPROVAL]} busy onDecide={noop} onAnswer={noop} />)

    expect((screen.getByRole('button', { name: '允许' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: '拒绝' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('多条待决审批纵向堆叠', () => {
    render(
      <ApprovalBar
        approvals={[APPROVAL, { ...APPROVAL, approvalId: 'a2', tool: 'write_file' }]}
        busy={false}
        onDecide={noop}
        onAnswer={noop}
      />,
    )

    expect(screen.getByText('请求执行：bash')).toBeTruthy()
    expect(screen.getByText('请求执行：write_file')).toBeTruthy()
  })
})

describe('提问形态（阶段 58）', () => {
  it('提问渲染成问题 + 选项按钮；点选项立刻回传', () => {
    const onAnswer = vi.fn()
    render(
      <ApprovalBar approvals={[QUESTION]} busy={false} onDecide={noop} onAnswer={onAnswer} />,
    )

    expect(screen.getByText('问你一句')).toBeTruthy()
    expect(screen.getByText('用哪个名字？')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '乙' }))

    expect(onAnswer).toHaveBeenCalledWith('q1', '乙')
  })

  it('自由回答：填了才能发，回车也算', () => {
    const onAnswer = vi.fn()
    render(
      <ApprovalBar approvals={[QUESTION]} busy={false} onDecide={noop} onAnswer={onAnswer} />,
    )

    const send = screen.getByRole('button', { name: '回答' })
    expect((send as HTMLButtonElement).disabled).toBe(true)

    const input = screen.getByLabelText('回答')
    fireEvent.change(input, { target: { value: '丙' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onAnswer).toHaveBeenCalledWith('q1', '丙')
  })

  it('裁决形态不显示问答输入框（两种待决不串台）', () => {
    render(<ApprovalBar approvals={[APPROVAL]} busy={false} onDecide={noop} onAnswer={noop} />)

    expect(screen.queryByLabelText('回答')).toBeNull()
    expect(screen.getByText(/请求执行/)).toBeTruthy()
  })

  it('两种待决同时挂着时各画各的', () => {
    render(
      <ApprovalBar approvals={[APPROVAL, QUESTION]} busy={false} onDecide={noop} onAnswer={noop} />,
    )

    expect(screen.getByText('请求执行：bash')).toBeTruthy()
    expect(screen.getByText('用哪个名字？')).toBeTruthy()
  })
})
