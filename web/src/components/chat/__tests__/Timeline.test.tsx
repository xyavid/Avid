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

/** 手搭一段「一轮跑完」的段落：读数显式给，折叠行的用时才可断言。 */
function turn(): TimelineItem[] {
  return [
    { kind: 'user', entryId: 'e1', text: '跑一下', ts: 1_000 },
    { kind: 'reasoning', text: '先想想', startedAt: 1_100, endedAt: 1_500, streaming: false },
    { kind: 'assistant', entryId: 'e2', text: '先读一遍。', streaming: false, ts: 1_600 },
    {
      kind: 'tool',
      callId: 'c1',
      name: 'read_file',
      args: '{"path":"/w/a.py"}',
      result: 'print(1)',
      status: 'ok',
      durationMs: 12,
      runs: [],
    },
    { kind: 'assistant', entryId: 'e3', text: '结论是 avid。', streaming: false, ts: 73_000 },
  ]
}

afterEach(cleanup)

describe('Timeline（段落 → 对话列）', () => {
  it('用户气泡 + 衬线正文；一轮里只有第一段正文带标识行', () => {
    render(
      <Timeline
        liveTail
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
        runs: [
          {
            task: '前端时间线',
            index: 0,
            items: [
              {
                kind: 'tool',
                callId: 'k1',
                name: 'edit_file',
                args: '{"path":"/w/a.tsx"}',
                result: null,
                status: 'ok',
                durationMs: null,
                runs: [],
              },
              {
                kind: 'tool',
                callId: 'k2',
                name: 'bash',
                args: '{"command":"pnpm verify"}',
                result: null,
                status: 'running',
                durationMs: null,
                runs: [],
              },
            ],
          },
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

  it('一次回话只挂一行动作：落在末段，复制的是整段原文', () => {
    const onBranch = vi.fn()
    const items: TimelineItem[] = [
      { kind: 'user', entryId: 'e1', text: '跑一下', ts: 1 },
      { kind: 'assistant', entryId: 'e2', text: '先读一遍。', streaming: false, ts: 2 },
      {
        kind: 'tool',
        callId: 'c1',
        name: 'read_file',
        args: '{"path":"/w/a.py"}',
        result: 'x',
        status: 'ok',
        durationMs: null,
        runs: [],
      },
      { kind: 'assistant', entryId: 'e3', text: '结论是 avid。', streaming: false, ts: 3 },
    ]
    render(<Timeline items={items} workspaceRoot="/w" onBranch={onBranch} />)

    // 用户那句话一行，整段回话一行——中间那段正文不各挂一排
    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: '分支' })).toHaveLength(1)

    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    fireEvent.click(screen.getAllByRole('button', { name: '复制' })[1]!)
    expect(writeText).toHaveBeenCalledWith('先读一遍。\n\n结论是 avid。')

    fireEvent.click(screen.getByRole('button', { name: '分支' }))
    expect(onBranch).toHaveBeenCalledWith('e3')
  })

  it('还在流的末段不出现分支钮（分叉点必须是已落库的条目）', () => {
    const items: TimelineItem[] = [
      { kind: 'user', entryId: null, text: '帮我读一下 pyproject.toml', ts: 1 },
      { kind: 'assistant', entryId: 'e2', text: '项目名是 avid。', streaming: false, ts: 2 },
      { kind: 'assistant', entryId: null, text: '正在写下一段', streaming: true, ts: null },
    ]
    render(<Timeline items={items} onBranch={vi.fn()} />)

    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '分支' })).toBeNull()
  })

  it('两轮对话各挂一行：复制数 = 用户消息数 + 回话数', () => {
    const items: TimelineItem[] = [
      { kind: 'user', entryId: 'e1', text: '第一问', ts: 1 },
      { kind: 'assistant', entryId: 'e2', text: '第一答', streaming: false, ts: 2 },
      { kind: 'user', entryId: 'e3', text: '第二问', ts: 3 },
      { kind: 'assistant', entryId: 'e4', text: '第二答', streaming: false, ts: 4 },
    ]
    render(<Timeline items={items} onBranch={vi.fn()} />)

    expect(screen.getAllByRole('button', { name: '复制' })).toHaveLength(4)
    expect(screen.getAllByRole('button', { name: '分支' })).toHaveLength(2)
  })

  it('跑完的轮折成一行：只留收尾正文，过程（思考 / 工具 / 中间正文）收进「已完成，用时 1分12秒」', () => {
    render(<Timeline items={turn()} workspaceRoot="/w" />)

    expect(screen.getByText('跑一下')).toBeTruthy()
    expect(screen.getByText('结论是 avid。')).toBeTruthy()
    expect(screen.getByText('已完成，用时 1分12秒')).toBeTruthy()
    expect(screen.queryByText('先读一遍。')).toBeNull()
    expect(screen.queryByText('读取')).toBeNull()
    expect(screen.queryByText('思考 · 400ms')).toBeNull()
  })

  it('折叠行是开合开关：点开还原这一轮的过程，再点收起', () => {
    render(<Timeline items={turn()} workspaceRoot="/w" />)
    const line = () => screen.getByRole('button', { name: /已完成/ })

    expect(line().getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText('先读一遍。')).toBeNull()

    fireEvent.click(line())
    expect(screen.getByText('先读一遍。')).toBeTruthy()
    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.getByText('思考 · 400ms')).toBeTruthy()
    expect(line().getAttribute('aria-expanded')).toBe('true')

    fireEvent.click(line())
    expect(screen.queryByText('先读一遍。')).toBeNull()
  })

  it('本轮还在跑不折：过程中逐段出现，收尾才收起来', () => {
    render(<Timeline liveTail items={turn()} workspaceRoot="/w" />)

    expect(screen.getByText('先读一遍。')).toBeTruthy()
    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /已完成/ })).toBeNull()
  })

  it('没有过程的轮不插折叠行（问一句答一句，别为折叠而折叠）', () => {
    render(
      <Timeline
        items={itemsFromEntries([
          entry(1, { role: 'user', content: '问' }),
          entry(2, { role: 'assistant', content: '答' }),
        ])}
      />,
    )

    expect(screen.getByText('答')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /已完成/ })).toBeNull()
  })

  it('末尾是工具（中断、失败）→ 没有收尾消息，整轮照常铺着', () => {
    const items = [...turn().slice(0, 4)]
    render(<Timeline items={items} workspaceRoot="/w" />)

    expect(screen.getByText('先读一遍。')).toBeTruthy()
    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /已完成/ })).toBeNull()
  })

  it('读数缺失时折叠行只说「已完成」（不编一个用时出来）', () => {
    const items = turn().map((item) =>
      item.kind === 'user' || item.kind === 'assistant' ? { ...item, ts: null } : item,
    )
    render(<Timeline items={items} workspaceRoot="/w" />)

    expect(screen.getByText('已完成')).toBeTruthy()
  })

  it('点子智能体卡：展开卡片，并把「打开右列」的通知发出去', () => {
    const onOpenSubagents = vi.fn()
    const items: TimelineItem[] = [
      { kind: 'user', entryId: 'e1', text: '派活', ts: 1 },
      {
        kind: 'tool',
        callId: 'call_sub',
        name: 'subagent',
        args: JSON.stringify({ tasks: [{ description: '统计 a.py', prompt: '...' }] }),
        result: '已运行 1 个 subagent：…',
        status: 'ok',
        durationMs: 900,
        runs: [
          {
            task: '统计 a.py',
            index: 0,
            items: [
              {
                kind: 'tool',
                callId: 'k1',
                name: 'read_file',
                args: '{"path":"/w/a.py"}',
                result: 'print(1)',
                status: 'ok',
                durationMs: 3,
                runs: [],
              },
            ],
          },
        ],
      },
      { kind: 'assistant', entryId: 'e2', text: '回来了。', streaming: false, ts: 2 },
    ]
    render(<Timeline liveTail items={items} workspaceRoot="/w" onOpenSubagents={onOpenSubagents} />)

    fireEvent.click(screen.getByRole('button', { name: /子智能体/ }))

    expect(onOpenSubagents).toHaveBeenCalledOnce()
    // 一次点击两个动作：卡片也展开（子步骤由 runs 派生）
    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.getByText('a.py')).toBeTruthy()
  })

  it('文件类工具卡展开成差异视图（编辑：参数里有旧文与新文）', () => {
    const items: TimelineItem[] = [
      { kind: 'user', entryId: 'e1', text: '改一下', ts: 1 },
      {
        kind: 'tool',
        callId: 'c1',
        name: 'edit_file',
        args: JSON.stringify({ path: '/w/a.py', old_string: 'a\nb\nc', new_string: 'a\nB\nc' }),
        result: '已替换 a.py 中的 1 处文本',
        status: 'ok',
        durationMs: 5,
        runs: [],
      },
      { kind: 'assistant', entryId: 'e2', text: '改好了。', streaming: false, ts: 2 },
    ]
    const { container } = render(<Timeline liveTail items={items} workspaceRoot="/w" />)

    // 折叠态只有一行，差异在点开之后
    expect(container.querySelectorAll('[data-diff]')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: /编辑/ }))

    const rows = [...container.querySelectorAll('[data-diff]')].map(
      (el) => `${el.getAttribute('data-diff')}|${el.textContent ?? ''}`,
    )
    expect(rows).toEqual(['context| a', 'del|-b', 'add|+B', 'context| c'])
    expect(screen.getByText('已替换 a.py 中的 1 处文本')).toBeTruthy()
  })
})
