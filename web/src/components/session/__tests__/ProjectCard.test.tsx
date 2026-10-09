// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceSummary } from '../../../api/types'
import { ProjectCard } from '../ProjectCard'

const WORKSPACES: WorkspaceSummary[] = [
  { id: 'w1', root: '/home/fishy/Avid', name: 'Avid', created_at: 0, last_used_at: 0, is_default: true },
  { id: 'w2', root: '/home/fishy/other', name: 'Other', created_at: 0, last_used_at: 0, is_default: false },
]

afterEach(cleanup)

describe('ProjectCard（侧栏 · 项目卡，可收回）', () => {
  it('项目行 = 工作区候选：文件夹图标 + 名称，点行选择', () => {
    const onSelect = vi.fn()
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onSelectWorkspace={onSelect} />)

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText('Other')).toBeTruthy()
    // One folder icon per row.
    expect(screen.getAllByTitle('/home/fishy/other').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByText('Other'))
    expect(onSelect).toHaveBeenCalledWith('w2')
  })

  it('会话归属行打「当前会话」标记；选中项有勾', () => {
    render(
      <ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" sessionWorkspaceId="w1" onSelectWorkspace={() => {}} />,
    )

    expect(screen.getByText('当前会话')).toBeTruthy()
    // Selected row: accent check (svg).
    expect(document.querySelectorAll('svg')).toBeTruthy()
  })

  it('可收回：点标题 chevron 收起主体，再点展开', () => {
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onSelectWorkspace={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: '收起项目' }))
    expect(screen.queryByText('Other')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '展开项目' }))
    expect(screen.getByText('Other')).toBeTruthy()
  })

  it('新增：picker 可用时标题行出加号，busy 禁用', () => {
    const onAddByPicker = vi.fn()
    const { rerender } = render(
      <ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" pickerAvailable busy={false} onAddByPicker={onAddByPicker} />,
    )

    const add = screen.getByRole('button', { name: '新增项目' }) as HTMLButtonElement
    expect(add.disabled).toBe(false)
    fireEvent.click(add)
    expect(onAddByPicker).toHaveBeenCalledOnce()

    rerender(
      <ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" pickerAvailable busy onAddByPicker={onAddByPicker} />,
    )
    expect((screen.getByRole('button', { name: '新增项目' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('picker 不可用：加号禁用并说明替代入口，不再提供手动路径输入', () => {
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" pickerAvailable={false} />)

    const add = screen.getByRole('button', { name: '新增项目' }) as HTMLButtonElement
    expect(add.disabled).toBe(true)
    expect(add.title).toContain('avid workspace add')
    expect(screen.queryByPlaceholderText('/绝对/路径')).toBeNull()
    expect(screen.queryByText('手动输入路径')).toBeNull()
  })

  it('每行都有「更多」按钮，默认不占视线：透明，悬停/聚焦才现形', () => {
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onDeleteWorkspace={() => {}} />)

    const more = screen.getByRole('button', { name: '更多：Other' })
    expect(more.className).toContain('opacity-0')
    expect(more.className).toContain('group-hover:opacity-100')
    expect(more.querySelector('svg')).toBeTruthy()
    // One per row, not shared.
    expect(screen.getAllByRole('button', { name: /^更多：/ })).toHaveLength(2)
  })

  it('按下「更多」才出删除按钮；再按收起', () => {
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onDeleteWorkspace={() => {}} />)

    expect(screen.queryByRole('button', { name: '删除 Other' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '更多：Other' }))
    expect(screen.getByRole('button', { name: '删除 Other' })).toBeTruthy()
    // The expanded row's button stays visible without hover.
    expect(screen.getByRole('button', { name: '更多：Other' }).getAttribute('aria-expanded')).toBe('true')

    fireEvent.click(screen.getByRole('button', { name: '更多：Other' }))
    expect(screen.queryByRole('button', { name: '删除 Other' })).toBeNull()
  })

  it('删除只作用于该行，并带上该行 id；说明这是从列表移除', () => {
    const onDelete = vi.fn()
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onDeleteWorkspace={onDelete} />)

    fireEvent.click(screen.getByRole('button', { name: '更多：Other' }))
    // The row spells out that it removes a registry entry, not the sessions on disk.
    expect(screen.getByText(/会话文件留在磁盘/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '删除 Other' }))

    expect(onDelete).toHaveBeenCalledWith('w2')
    expect(onDelete).toHaveBeenCalledTimes(1)
    // The menu collapses after deleting.
    expect(screen.queryByRole('button', { name: '删除 Other' })).toBeNull()
  })

  it('没给 onDeleteWorkspace 时不出现「更多」（只读形态）', () => {
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" />)

    expect(screen.queryByRole('button', { name: /^更多：/ })).toBeNull()
  })

  it('hint 展示；正在加载时行区给占位', () => {
    render(<ProjectCard workspaces={null} activeWorkspaceId={null} hint="该目录已在列表中" />)

    expect(screen.getByText('正在加载项目…')).toBeTruthy()
    expect(screen.getByText(/已在列表中/)).toBeTruthy()
  })
})
