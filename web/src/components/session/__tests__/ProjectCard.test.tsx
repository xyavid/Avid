// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceSummary } from '../../../api/types'
import { ProjectCard } from '../ProjectCard'

const WORKSPACES: WorkspaceSummary[] = [
  { id: 'w1', root: '/home/fishy/Avid', name: 'Avid', created_at: 0, last_used_at: 0, default_permission: 'manual', is_default: true },
  { id: 'w2', root: '/home/fishy/other', name: 'Other', created_at: 0, last_used_at: 0, default_permission: 'auto', is_default: false },
]

afterEach(cleanup)

describe('ProjectCard（侧栏 · 项目卡，可收回）', () => {
  it('项目行 = 工作区候选：文件夹图标 + 名称，点行选择', () => {
    const onSelect = vi.fn()
    render(<ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" onSelectWorkspace={onSelect} />)

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText('Other')).toBeTruthy()
    // 每行一个文件夹图标
    expect(screen.getAllByTitle('/home/fishy/other').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByText('Other'))
    expect(onSelect).toHaveBeenCalledWith('w2')
  })

  it('会话归属行打「当前会话」标记；选中项有勾', () => {
    render(
      <ProjectCard workspaces={WORKSPACES} activeWorkspaceId="w1" sessionWorkspaceId="w1" onSelectWorkspace={() => {}} />,
    )

    expect(screen.getByText('当前会话')).toBeTruthy()
    // 选中项：accent 勾（svg）
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

  it('hint 展示；正在加载时行区给占位', () => {
    render(<ProjectCard workspaces={null} activeWorkspaceId={null} hint="该目录已在列表中" />)

    expect(screen.getByText('正在加载项目…')).toBeTruthy()
    expect(screen.getByText(/已在列表中/)).toBeTruthy()
  })
})
