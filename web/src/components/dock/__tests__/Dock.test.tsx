// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dock } from '../Dock'
import type { DockPanelId } from '../../../state/dock'
import type { LiveTool, RunPhase } from '../../../state/useRunStream'

afterEach(cleanup)

const runningTools: LiveTool[] = [
  { callId: 'c1', tool: 'bash', status: 'running', arguments: '{}', result: null },
  { callId: 'c2', tool: 'read_file', status: 'ok', arguments: '{}', result: 'ok' },
]

function renderDock(overrides: {
  active?: DockPanelId
  phase?: RunPhase | null
  tools?: LiveTool[]
  approvals?: { approvalId: string; tool: string; arguments: string; reason: string }[]
  onSelect?: (id: DockPanelId) => void
  onDecide?: (id: string, decision: 'allow' | 'deny') => void
  workspaceRoot?: string | null
  workspaceId?: string | null
} = {}) {
  const { active = 'processes', phase = 'running' as RunPhase, tools = runningTools, approvals = [], onSelect = () => {}, onDecide = vi.fn() } = overrides
  return render(
    <Dock
      active={active}
      onSelect={onSelect}
      onClose={() => {}}
      phase={phase}
      tools={tools}
      approvals={approvals}
      onDecide={onDecide}
      workspaceRoot={'workspaceRoot' in overrides ? (overrides.workspaceRoot ?? null) : '/tmp/ws'}
      workspaceId={'workspaceId' in overrides ? (overrides.workspaceId ?? null) : null}
    />,
  )
}

describe('右侧 dock（阶段 48；阶段 52 起是常驻右列而非浮层）', () => {
  it('是列不是浮层：占位（无 fixed/translate），收起由页面决定（不挂它）', () => {
    const { container } = renderDock()

    const aside = container.querySelector('aside') as HTMLElement
    expect(aside.className).toContain('h-full')
    expect(aside.className).toContain('border-l')
    expect(aside.className).not.toContain('fixed')
    expect(aside.className).not.toContain('translate')
    expect(aside.getAttribute('aria-hidden')).toBeNull()
  })

  it('进程面板：状态行（进程/工具/审批）+ 工具迷你列表', () => {
    renderDock()

    // 「进程」在这一屏出现两次：头部（当前面板名）与状态行标签
    expect(screen.getAllByText('进程')).toHaveLength(2)
    expect(screen.getByText('运行中')).toBeTruthy()
    expect(screen.getByText(/2 个 · 1 运行/)).toBeTruthy()
    expect(screen.getByText('bash')).toBeTruthy()
    expect(screen.getByText('read_file')).toBeTruthy()
  })

  it('进程面板空闲态：无活运行给「空闲」与空态文案', () => {
    renderDock({ phase: null, tools: [] })

    expect(screen.getByText('空闲')).toBeTruthy()
    expect(screen.getByText('没有正在运行的进程')).toBeTruthy()
  })

  it('审查面板：待决审批复用审批条（两步确认），拒绝直达 onDecide；空态给文案', () => {
    const onDecide = vi.fn()
    const approvals = [{ approvalId: 'a1', tool: 'bash', arguments: '{}', reason: '递归删除根目录' }]
    const { rerender } = renderDock({ active: 'review', approvals, onDecide })

    expect(screen.getByText(/bash/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /允许/ }))
    expect(onDecide).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认执行' }))
    expect(onDecide).toHaveBeenCalledWith('a1', 'allow')

    rerender(
      <Dock
        active="review"
        onSelect={() => {}}
        onClose={() => {}}
          phase={null}
        tools={[]}
        approvals={[]}
        onDecide={onDecide}
        workspaceRoot="/tmp/ws"
        workspaceId={null}
      />,
    )
    expect(screen.getByText('没有待决审批')).toBeTruthy()
  })


  it('Esc 关闭：挂着就监听（收起时页面不挂它）', () => {
    const onClose = vi.fn()
    render(
      <Dock
        active="processes"
        onSelect={() => {}}
        onClose={onClose}
          phase="running"
        tools={[]}
        approvals={[]}
        onDecide={() => {}}
        workspaceRoot="/tmp/ws"
        workspaceId={null}
      />,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()
  })
})


describe('dock 重面板（阶段 49）', () => {
  afterEach(cleanup)

  it('终端面板：无工作区时空态，不拉 xterm（lazy 面板等 Suspense 落定）', async () => {
    renderDock({ active: 'terminal', phase: null, tools: [], workspaceRoot: null })

    expect(await screen.findByText('未选择工作区')).toBeTruthy()
    expect(screen.queryByText('连接中…')).toBeNull()
  })

  it('头部是当前面板 + 回列表；列表四个入口，上下文与浏览器都不在', () => {
    const onSelect = vi.fn()
    renderDock({ active: 'files', onSelect })

    // 默认常驻的是工作区文件（头部写着它），点回列表看四个入口
    expect(screen.getByRole('button', { name: '回到面板列表' }).textContent).toContain('工作区文件')
    fireEvent.click(screen.getByRole('button', { name: '回到面板列表' }))

    const list = screen.getByRole('navigation', { name: '面板列表' })
    const labels = [...list.querySelectorAll('button')].map((item) => item.textContent ?? '')
    expect(labels).toHaveLength(4)
    expect(labels[0]).toContain('工作区文件')
    expect(labels[1]).toContain('进程')
    expect(labels.join(' ')).not.toContain('上下文')
    expect(labels.join(' ')).not.toContain('浏览器')

    // 选一个条目就把选择交出去，列表收起（面板内容由页面按 active 渲染）
    fireEvent.click([...list.querySelectorAll('button')][2]!)
    expect(onSelect).toHaveBeenCalledWith('review')
    expect(screen.queryByRole('navigation', { name: '面板列表' })).toBeNull()
  })
})
