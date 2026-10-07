// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Dock } from '../Dock'
import type { DockPanelId } from '../../../state/dock'
import type { LiveTool, RunPhase } from '../../../state/useRunStream'
import type { UsageReport } from '../../../api/types'

afterEach(cleanup)

const usage: UsageReport = {
  context: { tokens: 1200, window: 65536, utilization: 0.02, parts: null },
  cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
  compaction: { count: 0, last_compaction_tokens: null, last_step: null },
}

const runningTools: LiveTool[] = [
  { callId: 'c1', tool: 'bash', status: 'running', arguments: '{}', result: null },
  { callId: 'c2', tool: 'read_file', status: 'ok', arguments: '{}', result: 'ok' },
]

function renderDock(overrides: {
  open?: boolean
  active?: DockPanelId
  phase?: RunPhase | null
  tools?: LiveTool[]
  approvals?: { approvalId: string; tool: string; arguments: string; reason: string }[]
  onDecide?: (id: string, decision: 'allow' | 'deny') => void
  workspaceRoot?: string | null
} = {}) {
  const { open = true, active = 'processes', phase = 'running' as RunPhase, tools = runningTools, approvals = [], onDecide = vi.fn() } = overrides
  return render(
    <Dock
      open={open}
      active={active}
      onSelect={() => {}}
      onClose={() => {}}
      usage={usage}
      phase={phase}
      tools={tools}
      approvals={approvals}
      onDecide={onDecide}
      workspaceRoot={'workspaceRoot' in overrides ? (overrides.workspaceRoot ?? null) : '/tmp/ws'}
    />,
  )
}

describe('右侧 dock（阶段 48）', () => {
  it('收起时移出视口且 aria-hidden，不再接收指针', () => {
    const { container } = renderDock({ open: false })

    const aside = container.querySelector('aside') as HTMLElement
    expect(aside.getAttribute('aria-hidden')).toBe('true')
    expect(aside.className).toContain('translate-x-full')
    expect(aside.className).toContain('pointer-events-none')
  })

  it('进程面板：状态行（进程/工具/审批）+ 工具迷你列表', () => {
    renderDock()

    expect(screen.getByText('进程').textContent).toContain('进程')
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
        open
        active="review"
        onSelect={() => {}}
        onClose={() => {}}
        usage={usage}
        phase={null}
        tools={[]}
        approvals={[]}
        onDecide={onDecide}
        workspaceRoot="/tmp/ws"
      />,
    )
    expect(screen.getByText('没有待决审批')).toBeTruthy()
  })

  it('上下文面板：复用 ContextRail 展示用量读数', () => {
    renderDock({ active: 'context' })

    expect(screen.getByText('已用 tokens')).toBeTruthy()
    expect(screen.getByText('1,200')).toBeTruthy()
  })

  it('Esc 关闭：仅展开时监听', () => {
    const onClose = vi.fn()
    const { rerender } = render(
      <Dock
        open
        active="processes"
        onSelect={() => {}}
        onClose={onClose}
        usage={usage}
        phase="running"
        tools={[]}
        approvals={[]}
        onDecide={() => {}}
        workspaceRoot="/tmp/ws"
      />,
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledOnce()

    rerender(
      <Dock
        open={false}
        active="processes"
        onSelect={() => {}}
        onClose={onClose}
        usage={usage}
        phase="running"
        tools={[]}
        approvals={[]}
        onDecide={() => {}}
        workspaceRoot="/tmp/ws"
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

  it('浏览器面板：非法 scheme 拒绝；合法 URL 渲染 iframe 与新窗口兜底', async () => {
    const { default: BrowserPanel } = await import('../BrowserPanel')
    render(<BrowserPanel />)


    const address = screen.getByLabelText('浏览器地址') as HTMLInputElement
    fireEvent.change(address, { target: { value: 'javascript:alert(1)' } })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    expect(screen.getByText(/地址不合法/)).toBeTruthy()
    expect(screen.queryByTitle('浏览器面板')).toBeNull()

    fireEvent.change(address, { target: { value: 'localhost:8799' } })
    fireEvent.click(screen.getByRole('button', { name: '打开' }))
    const frame = screen.getByTitle('浏览器面板') as HTMLIFrameElement
    expect(frame.getAttribute('src')).toBe('https://localhost:8799/')
    expect(screen.getByText('新窗口打开')).toBeTruthy()
  })

  it('页签扩展：终端与浏览器都在标签条里', () => {
    renderDock({ active: 'context' })

    expect(screen.getByTitle('终端')).toBeTruthy()
    expect(screen.getByTitle('浏览器')).toBeTruthy()
  })
})
