// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { Meta, SessionSummary, UsageReport, WorkspaceSummary } from '../../../api/types'
import { ContextRail } from '../ContextRail'

const USAGE: UsageReport = {
  context: { tokens: 5200, window: 400000, utilization: 0.013, parts: null },
  cache: { read_tokens: null, write_tokens: null, hit_ratio: 0.808 },
  compaction: { count: 0, last_compaction_tokens: null, last_step: null },
}

const META = { capabilities: { model: 'deepseek/x', tools: [], skills: [], sandbox: null } } as unknown as Meta

const SESSION = {
  id: 's1',
  workspace: { id: 'w1', root: '/home/fishy/Avid', name: 'Avid', default_permission: 'manual' },
} as unknown as SessionSummary

describe('ContextRail（右栏）', () => {
  afterEach(cleanup)

  it('上下文卡只放上下文读数：窗口/已用/占用率/缓存命中', () => {
    render(<ContextRail meta={META} session={SESSION} usage={USAGE} />)

    expect(screen.getByText('已用 tokens').nextElementSibling?.textContent).toBe('5,200')
    expect(screen.getByText('上下文窗口').nextElementSibling?.textContent).toBe('400,000')
    expect(screen.getByText('占用率').nextElementSibling?.textContent).toBe('1.3%')
    expect(screen.getByText('缓存命中率').nextElementSibling?.textContent).toBe('80.8%')
  })

  it('不要模型/工具/技能/沙箱——它们不属于上下文卡', () => {
    render(<ContextRail meta={META} session={SESSION} usage={USAGE} />)

    expect(screen.queryByText('模型')).toBeNull()
    expect(screen.queryByText('工具')).toBeNull()
    expect(screen.queryByText('技能')).toBeNull()
    expect(screen.queryByText('沙箱')).toBeNull()
  })

  it('没有用量快照时显示 —（未上报 ≠ 0）', () => {
    render(<ContextRail meta={META} session={SESSION} usage={null} />)

    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(4)
  })

  it('工作区卡保留：名称与路径来自选中会话', () => {
    render(<ContextRail meta={META} session={SESSION} usage={USAGE} />)

    expect(screen.getByText('工作区')).toBeTruthy()
    expect(screen.getByText('/home/fishy/Avid')).toBeTruthy()
  })

  const WORKSPACES: WorkspaceSummary[] = [
    { id: 'w1', root: '/home/fishy/Avid', name: 'Avid', created_at: 0, last_used_at: 0, default_permission: 'manual', is_default: true },
    { id: 'w2', root: '/home/fishy/other', name: 'Other', created_at: 0, last_used_at: 0, default_permission: 'auto', is_default: false },
  ]

  it('列出候选工作区并可选择；会话归属打「当前会话」标记', () => {
    const onSelect = vi.fn()
    render(
      <ContextRail
        meta={META}
        session={SESSION}
        usage={USAGE}
        workspaces={WORKSPACES}
        activeWorkspaceId="w1"
        sessionWorkspaceId="w1"
        onSelectWorkspace={onSelect}
      />,
    )

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText('Other')).toBeTruthy()
    expect(screen.getByText('当前会话')).toBeTruthy()

    fireEvent.click(screen.getByText('Other'))
    expect(onSelect).toHaveBeenCalledWith('w2')
  })

  it('picker 可用：标题行出「新增」按钮，busy 时禁用', () => {
    const onAddByPicker = vi.fn()
    const { rerender } = render(
      <ContextRail
        meta={META}
        session={SESSION}
        usage={USAGE}
        workspaces={WORKSPACES}
        pickerAvailable
        busy={false}
        onAddByPicker={onAddByPicker}
      />,
    )

    const add = screen.getByRole('button', { name: '新增' }) as HTMLButtonElement
    expect(add.disabled).toBe(false)
    fireEvent.click(add)
    expect(onAddByPicker).toHaveBeenCalledOnce()

    rerender(
      <ContextRail
        meta={META}
        session={SESSION}
        usage={USAGE}
        workspaces={WORKSPACES}
        pickerAvailable
        busy
        onAddByPicker={onAddByPicker}
      />,
    )
    expect((screen.getByRole('button', { name: '新增' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('picker 不可用：出手动路径输入行，确认带路径回调', () => {
    const onAddByPath = vi.fn()
    render(
      <ContextRail
        meta={META}
        session={SESSION}
        usage={USAGE}
        workspaces={WORKSPACES}
        pickerAvailable={false}
        onAddByPath={onAddByPath}
      />,
    )

    const input = screen.getByPlaceholderText('/绝对/路径') as HTMLInputElement
    fireEvent.change(input, { target: { value: '/tmp/new-ws' } })
    fireEvent.click(screen.getByRole('button', { name: '确认' }))
    expect(onAddByPath).toHaveBeenCalledWith('/tmp/new-ws')
  })

  it('hint 展示（如 409 已存在时页面给的话）', () => {
    render(
      <ContextRail
        meta={META}
        session={SESSION}
        usage={USAGE}
        workspaces={WORKSPACES}
        hint="该目录已在列表中，已为你选中"
      />,
    )

    expect(screen.getByText(/已在列表中/)).toBeTruthy()
  })
})
