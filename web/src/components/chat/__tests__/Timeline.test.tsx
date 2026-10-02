// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Entry } from '../../../api/types'
import { Timeline } from '../Timeline'

function entry(seq: number, message: Record<string, unknown>): Entry {
  return { entry_id: `e${seq}`, parent_id: null, seq, timestamp: 0, type: 'message', message }
}

afterEach(cleanup)

describe('Timeline（durable 条目 → 冻结组件）', () => {
  it('user → 气泡；assistant 纯文本 → 衬线回复', () => {
    render(
      <Timeline
        entries={[
          entry(1, { role: 'user', content: '跑一下' }),
          entry(4, { role: 'assistant', content: '做完了' }),
        ]}
      />,
    )

    expect(screen.getByText('跑一下')).toBeTruthy()
    expect(screen.getByText('做完了')).toBeTruthy()
  })

  it('单条工具调用 → 折叠行：标题 + 结果首行预览 + 成功勾，不渲染组头', () => {
    render(
      <Timeline
        entries={[
          entry(1, { role: 'user', content: '跑一下' }),
          entry(2, {
            role: 'assistant',
            content: '',
            tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'bash', arguments: '{"command": "echo hi"}' } }],
          }),
          entry(3, { role: 'tool', tool_call_id: 'call_1', content: 'bash ok' }),
        ]}
      />,
    )

    expect(screen.getByText('bash')).toBeTruthy()
    expect(screen.getByText('bash ok')).toBeTruthy()
    expect(screen.getByLabelText('成功')).toBeTruthy()
    expect(screen.queryByText(/个工具/)).toBeNull()
  })

  it('连续多条工具调用 → 聚成「N 个工具」组，组头可开关整组', () => {
    render(
      <Timeline
        entries={[
          entry(2, {
            role: 'assistant',
            content: '',
            tool_calls: [
              { id: 'c1', type: 'function', function: { name: 'bash', arguments: '{"command":"echo one"}' } },
              { id: 'c2', type: 'function', function: { name: 'bash', arguments: '{"command":"echo two"}' } },
            ],
          }),
          entry(3, { role: 'tool', tool_call_id: 'c1', content: 'one' }),
          entry(4, { role: 'tool', tool_call_id: 'c2', content: 'two' }),
        ]}
      />,
    )

    expect(screen.getByText('2 个工具')).toBeTruthy()
    expect(screen.getByText('one')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /2 个工具/ }))
    expect(screen.queryByText('one')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /2 个工具/ }))
    expect(screen.getByText('two')).toBeTruthy()
  })

  it('失败结果（错误：前缀）标失败叉；中断批次标运行中并显示参数', () => {
    render(
      <Timeline
        entries={[
          entry(2, {
            role: 'assistant',
            content: '',
            tool_calls: [
              { id: 'c1', type: 'function', function: { name: 'bash', arguments: '{"command":"boom"}' } },
              { id: 'c2', type: 'function', function: { name: 'bash', arguments: '{"command":"echo hi"}' } },
            ],
          }),
          entry(3, { role: 'tool', tool_call_id: 'c1', content: '错误：命令被拒绝' }),
        ]}
      />,
    )

    expect(screen.getByLabelText('失败')).toBeTruthy()
    expect(screen.getByLabelText('运行中')).toBeTruthy()
    expect(screen.getByText('{"command":"echo hi"}')).toBeTruthy()
  })

  it('notice 条目不进对话视图', () => {
    render(
      <Timeline
        entries={[
          entry(1, { role: 'user', content: '在吗' }),
          { entry_id: 'n1', parent_id: null, seq: 2, timestamp: 0, type: 'notice', message: { role: 'user', content: '[提醒] 连续三轮未更新清单' } },
        ]}
      />,
    )

    expect(screen.getByText('在吗')).toBeTruthy()
    expect(screen.queryByText(/提醒/)).toBeNull()
  })
})

describe('Timeline · 消息动作行（阶段 14）', () => {
  const entries = [
    entry(1, { role: 'user', content: '帮我读一下 pyproject.toml' }),
    entry(2, { role: 'assistant', content: '项目名是 avid。' }),
  ]

  it('助手消息底部有复制与分支；用户消息只有复制', () => {
    const onBranch = vi.fn()
    render(<Timeline entries={entries} onBranch={onBranch} />)

    // 两条消息各一个动作行；分支按钮只有一个（助手那条）
    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: '分支' })).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '分支' }))
    expect(onBranch).toHaveBeenCalledWith('e2')
  })

  it('没给 onBranch 时一条分支按钮都没有（只读呈现）', () => {
    render(<Timeline entries={entries} />)

    expect(screen.queryByRole('button', { name: '分支' })).toBeNull()
    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(2)
  })
})
