// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Entry } from '../../../api/types'
import type { TimelineItem } from '../../../state/timeline'
import { itemsFromEntries } from '../../../state/timeline'
import { Timeline } from '../Timeline'

function entry(seq: number, message: Record<string, unknown>, type = 'message'): Entry {
  return { entry_id: `e${seq}`, parent_id: null, seq, timestamp: seq, type, message }
}

function toolCall(id: string, name: string, args: Record<string, unknown>) {
  return { id, type: 'function', function: { name, arguments: JSON.stringify(args) } }
}

afterEach(cleanup)

describe('Timeline（段落 → 对话列）', () => {
  it('用户气泡 + 衬线正文；一轮里只有第一段正文带标识行', () => {
    render(
      <Timeline
        items={itemsFromEntries([
          entry(1, { role: 'user', content: '跑一下' }),
          entry(2, { role: 'assistant', content: '先跑。' }),
          entry(3, { role: 'assistant', content: '跑完了。' }),
        ])}
      />,
    )

    expect(screen.getByText('跑一下')).toBeTruthy()
    expect(screen.getByText('先跑。')).toBeTruthy()
    expect(screen.getByText('跑完了。')).toBeTruthy()
    expect(screen.getAllByText('Avid')).toHaveLength(1)
  })

  it('工具行是「动作 + 目标」，连续调用不再聚成「N 个工具」', () => {
    render(
      <Timeline
        workspaceRoot="/w"
        items={itemsFromEntries([
          entry(1, { role: 'user', content: '跑一下' }),
          entry(2, {
            role: 'assistant',
            content: '先查一眼。',
            tool_calls: [
              toolCall('c1', 'read_file', { path: '/w/tests/a.py' }),
              toolCall('c2', 'bash', { command: 'uv run pytest -q' }),
            ],
          }),
          entry(3, { role: 'tool', tool_call_id: 'c1', content: 'print(1)' }),
          entry(4, { role: 'tool', tool_call_id: 'c2', content: '271 passed' }),
        ])}
      />,
    )

    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.getByText('tests/a.py')).toBeTruthy()
    expect(screen.getByText('执行')).toBeTruthy()
    expect(screen.getByText('uv run pytest -q')).toBeTruthy()
    expect(screen.queryByText(/个工具/)).toBeNull()
  })

  it('工具行的状态与结果：失败叉、运行中呼吸点；结果在展开态', () => {
    render(
      <Timeline
        items={itemsFromEntries([
          entry(2, {
            role: 'assistant',
            content: '',
            tool_calls: [toolCall('c1', 'bash', { command: 'boom' }), toolCall('c2', 'bash', { command: 'never' })],
          }),
          entry(3, { role: 'tool', tool_call_id: 'c1', content: '错误：命令被拒绝' }),
        ])}
      />,
    )

    expect(screen.getByLabelText('失败')).toBeTruthy()
    expect(screen.getByLabelText('运行中')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /boom/ }))
    expect(screen.getByText('错误：命令被拒绝')).toBeTruthy()
  })

  it('思考段折成一行并带持续时长（live-only 段的形态）', () => {
    const items: TimelineItem[] = [
      { kind: 'reasoning', text: '先想想', startedAt: 1000, endedAt: 4200, streaming: false },
    ]
    render(<Timeline items={items} />)

    expect(screen.getByText('思考 · 3.2s')).toBeTruthy()
  })

  it('subagent 卡：折叠行给任务名，展开后按任务分组列出子步骤', () => {
    const items: TimelineItem[] = [
      {
        kind: 'tool',
        callId: 'sub',
        name: 'subagent',
        args: JSON.stringify({ tasks: [{ description: '前端时间线', prompt: '...' }] }),
        result: '子任务都回来了',
        status: 'running',
        durationMs: null,
        steps: [
          { task: '前端时间线', callId: 'k1', name: 'edit_file', args: '{"path":"/w/a.tsx"}', status: 'ok' },
          { task: '前端时间线', callId: 'k2', name: 'bash', args: '{"command":"pnpm verify"}', status: 'running' },
        ],
      },
    ]
    render(<Timeline items={items} workspaceRoot="/w" />)

    expect(screen.getByText('子智能体')).toBeTruthy()
    expect(screen.getByText('前端时间线')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /子智能体/ }))
    expect(screen.getByText('编辑')).toBeTruthy()
    expect(screen.getByText('a.tsx')).toBeTruthy()
    expect(screen.getByText('pnpm verify')).toBeTruthy()
    expect(screen.getByText('子任务都回来了')).toBeTruthy()
  })

  it('notice 条目不进时间线', () => {
    render(
      <Timeline
        items={itemsFromEntries([
          entry(1, { role: 'user', content: '在吗' }),
          entry(2, { role: 'user', content: '[提醒] 连续三轮未更新清单' }, 'notice'),
        ])}
      />,
    )

    expect(screen.getByText('在吗')).toBeTruthy()
    expect(screen.queryByText(/提醒/)).toBeNull()
  })

  it('流式正文带光标；只有已落库的助手段给「分支」', () => {
    const onBranch = vi.fn()
    const items: TimelineItem[] = [
      { kind: 'user', entryId: null, text: '帮我读一下 pyproject.toml' },
      { kind: 'assistant', entryId: 'e2', text: '项目名是 avid。', streaming: false },
      { kind: 'assistant', entryId: null, text: '正在写下一段', streaming: true },
    ]
    render(<Timeline items={items} onBranch={onBranch} />)

    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(3)
    expect(screen.getAllByRole('button', { name: '分支' })).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: '分支' }))
    expect(onBranch).toHaveBeenCalledWith('e2')
  })
})
