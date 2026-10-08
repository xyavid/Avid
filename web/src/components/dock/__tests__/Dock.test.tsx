// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DockPanelId } from '../../../state/dock'
import type { SubagentRunView } from '../../../state/timeline'
import { Dock } from '../Dock'

afterEach(cleanup)

function run(overrides: Partial<SubagentRunView> = {}): SubagentRunView {
  return { callId: 'call_sub', task: '统计 a.py', index: 0, items: [], running: false, ...overrides }
}

function renderDock(
  overrides: {
    active?: DockPanelId
    choosing?: boolean
    runs?: SubagentRunView[]
    live?: boolean
    onSelect?: (id: DockPanelId) => void
    onChoose?: (on: boolean) => void
    workspaceRoot?: string | null
    workspaceId?: string | null
  } = {},
) {
  const {
    active = 'files',
    choosing = false,
    runs = [],
    live = false,
    onSelect = () => {},
    onChoose = () => {},
  } = overrides
  return render(
    <Dock
      active={active}
      choosing={choosing}
      onChoose={onChoose}
      onSelect={onSelect}
      onClose={() => {}}
      workspaceRoot={'workspaceRoot' in overrides ? (overrides.workspaceRoot ?? null) : '/tmp/ws'}
      workspaceId={'workspaceId' in overrides ? (overrides.workspaceId ?? null) : null}
      subagentRuns={runs}
      live={live}
      sourceSessionId={null}
      model={null}
    />,
  )
}

describe('右侧 dock（阶段 48；阶段 53 起只留三个面板）', () => {
  it('是列不是浮层：占位（无 fixed/translate），收起由页面决定（不挂它）', () => {
    const { container } = renderDock()

    const aside = container.querySelector('aside') as HTMLElement
    expect(aside.className).toContain('h-full')
    expect(aside.className).toContain('border-l')
    expect(aside.className).not.toContain('fixed')
    expect(aside.className).not.toContain('translate')
    expect(aside.getAttribute('aria-hidden')).toBeNull()
  })

  it('手动打开先给选择页：choosing 为真时第一屏就是面板列表', () => {
    renderDock({ choosing: true, active: 'files' })

    expect(screen.getByRole('navigation', { name: '面板列表' })).toBeTruthy()
  })

  it('面板列表四个入口：工作区文件 / 子智能体 / 临时对话 / 终端（进程与审查已删）', () => {
    const onSelect = vi.fn()
    renderDock({ choosing: true, active: 'files', onSelect })

    const list = screen.getByRole('navigation', { name: '面板列表' })
    const labels = [...list.querySelectorAll('button')].map((item) => item.textContent ?? '')

    expect(labels).toHaveLength(4)
    expect(labels[0]).toContain('工作区文件')
    expect(labels[1]).toContain('子智能体')
    expect(labels[2]).toContain('临时对话')
    expect(labels[3]).toContain('终端')
    expect(labels.join(' ')).not.toContain('进程')
    expect(labels.join(' ')).not.toContain('审查')

    // 选一个条目就把选择交出去（收起列表由页面的 select 负责，组件不留状态）
    fireEvent.click([...list.querySelectorAll('button')][1]!)
    expect(onSelect).toHaveBeenCalledWith('subagents')
  })

  it('停在面板上时，头部那个 chevron 是回选择页的路', () => {
    const onChoose = vi.fn()
    renderDock({ choosing: false, active: 'files', onChoose })

    fireEvent.click(screen.getByRole('button', { name: '回到面板列表' }))
    expect(onChoose).toHaveBeenCalledWith(true)
  })

  it('子智能体面板：没派过就说清没有；派过列任务卡（步数；没明细的那条只在 title 里解释）', () => {
    const empty = renderDock({ active: 'subagents', runs: [] })
    expect(screen.getByText('还没有子智能体')).toBeTruthy()
    empty.unmount()

    renderDock({
      active: 'subagents',
      live: true,
      runs: [
        run({
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
          running: true,
        }),
        run({ callId: 'call_sub', task: '统计 b.py', index: 1 }),
      ],
    })

    expect(screen.getByText('统计 a.py')).toBeTruthy()
    expect(screen.getByText('1 步')).toBeTruthy()
    expect(screen.getByLabelText('运行中')).toBeTruthy()
    // 只落了任务清单的那条：版面不写解释，事实挂在 title 上
    expect(screen.getByText('统计 b.py').closest('button')?.getAttribute('title')).toContain('明细不落库')
    // 0 步的那条不再挂一行说明（界面只摆操作需要的）
    expect(screen.queryByText('明细不落库（只有任务清单）')).toBeNull()
  })

  it('点一条子任务进详情：它自己的时间线（复用对话列的渲染器），能回列表', () => {
    renderDock({
      active: 'subagents',
      workspaceRoot: '/w',
      runs: [
        run({
          items: [
            { kind: 'assistant', entryId: null, text: '我看一眼。', streaming: false, ts: null },
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
        }),
      ],
    })

    fireEvent.click(screen.getByRole('button', { name: /统计 a\.py/ }))

    expect(screen.getByText('我看一眼。')).toBeTruthy()
    expect(screen.getByText('读取')).toBeTruthy()
    expect(screen.getByText('a.py')).toBeTruthy()
    // 子时间线用另一个 testid：验收脚本要能把它与主对话列分开
    expect(document.querySelector('[data-testid="timeline-subagent"]')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '回到子任务列表' }))
    expect(screen.getByRole('navigation', { name: '子任务列表' })).toBeTruthy()
  })

  it('明细不在（刷新后的历史会话）时详情说实话，不画空时间线', () => {
    renderDock({ active: 'subagents', runs: [run()] })

    fireEvent.click(screen.getByRole('button', { name: /统计 a\.py/ }))

    const empty = screen.getByText('没有可显示的明细')
    expect(empty.getAttribute('title')).toContain('明细不落库')
    expect(document.querySelector('[data-testid="timeline-subagent"]')).toBeNull()
  })

  it('Esc 关闭：挂着就监听（收起时页面不挂它）', () => {
    const onClose = vi.fn()
    render(
      <Dock
        active="files"
        choosing={false}
        onChoose={() => {}}
        onSelect={() => {}}
        onClose={onClose}
        workspaceRoot="/tmp/ws"
        workspaceId={null}
        subagentRuns={[]}
        live={false}
        sourceSessionId={null}
        model={null}
      />,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})

describe('dock 重面板（阶段 49）', () => {
  it('终端面板：无工作区时空态，不拉 xterm（lazy 面板等 Suspense 落定）', async () => {
    renderDock({ active: 'terminal', workspaceRoot: null })

    expect(await screen.findByText('未选择工作区')).toBeTruthy()
    expect(screen.queryByText('连接中…')).toBeNull()
  })
})
