// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

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

  it('assistant 的 tool_calls → 工具卡；role:tool 结果按 id 归位进卡身', () => {
    const { container } = render(
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
    // 结果进卡身，而不是渲染成独立消息
    expect(screen.getByText(/bash ok/)).toBeTruthy()
    // 空内容的 tool_calls 轮次不渲染空气泡（本轮没有衬线正文块）
    expect(container.querySelectorAll('.serif-text')).toHaveLength(0)
  })

  it('结果未到（中断的批次）：卡身显示参数预览', () => {
    render(
      <Timeline
        entries={[
          entry(2, {
            role: 'assistant',
            content: '',
            tool_calls: [{ id: 'call_1', type: 'function', function: { name: 'bash', arguments: '{"command": "echo hi"}' } }],
          }),
        ]}
      />,
    )

    expect(screen.getByText(/echo hi/)).toBeTruthy()
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
