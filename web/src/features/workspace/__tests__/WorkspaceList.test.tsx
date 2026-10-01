// @vitest-environment jsdom
/**
 * 工作区列表的用例。
 *
 * 断言的重点是**权限与语义**，不是外观：进程绑定的那个工作区删不掉（服务端 409
 * `workspace_bound`），所以按钮必须真的禁用；移除按钮的 `aria-label` 必须带上
 * 名称——列表里只有名字和路径，屏幕阅读器用户在按钮上读不到"移除哪一个"。
 */

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceSummary } from '../../../api/types'
import { WorkspaceList } from '../components/WorkspaceList'

afterEach(cleanup)

function ws(id: string, root: string, name: string | null, extra: Partial<WorkspaceSummary> = {}): WorkspaceSummary {
  return {
    id,
    root,
    name,
    created_at: 0,
    last_used_at: 0,
    default_permission: null,
    is_default: false,
    ...extra,
  }
}

describe('WorkspaceList', () => {
  it('列表为空时给空态文案', () => {
    render(<WorkspaceList workspaces={[]} onRemove={vi.fn()} onAdd={vi.fn()} />)

    expect(screen.getByText('还没有登记任何工作区')).toBeTruthy()
  })

  it('name 为 null 时回落到 root 末段，root 全文进 title', () => {
    render(
      <WorkspaceList
        workspaces={[ws('w1', '/home/fishy/Avid', null)]}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    expect(screen.getByText('Avid')).toBeTruthy()
    expect(screen.getByText('/home/fishy/Avid').getAttribute('title')).toBe('/home/fishy/Avid')
  })

  it('进程绑定的工作区不可移除，并给出理由', () => {
    const onRemove = vi.fn()
    render(
      <WorkspaceList
        workspaces={[ws('w1', '/home/fishy/Avid', 'Avid', { is_default: true })]}
        onRemove={onRemove}
        onAdd={vi.fn()}
      />,
    )

    const button = screen.getByLabelText('移除工作区 Avid') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.getAttribute('title')).toContain('进程绑定')
    expect(screen.getByText('进程绑定')).toBeTruthy()

    fireEvent.click(button)
    expect(onRemove).not.toHaveBeenCalled()
  })

  it('普通工作区可以移除，回调带 id', () => {
    const onRemove = vi.fn()
    render(
      <WorkspaceList
        workspaces={[ws('w2', '/tmp/x', '临时')]}
        onRemove={onRemove}
        onAdd={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByLabelText('移除工作区 临时'))

    expect(onRemove).toHaveBeenCalledWith('w2')
  })

  it('默认权限只显示 manual / auto 两档，null 不显示徽标', () => {
    render(
      <WorkspaceList
        workspaces={[
          ws('w1', '/a', '甲', { default_permission: 'manual' }),
          ws('w2', '/b', '乙', { default_permission: 'auto' }),
          ws('w3', '/c', '丙'),
        ]}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
      />,
    )

    expect(screen.getByText('手动')).toBeTruthy()
    expect(screen.getByText('自动')).toBeTruthy()
    // 权限为 null = 没记过，不该凭空显示一档
    expect(screen.queryByText('默认')).toBeNull()
  })

  it('busy 时移除按钮禁用（防止一次删除投递两遍）', () => {
    render(
      <WorkspaceList
        workspaces={[ws('w2', '/tmp/x', '临时')]}
        onRemove={vi.fn()}
        onAdd={vi.fn()}
        busy
      />,
    )

    expect((screen.getByLabelText('移除工作区 临时') as HTMLButtonElement).disabled).toBe(true)
  })

  it('「添加工作区」触发 onAdd', () => {
    const onAdd = vi.fn()
    render(<WorkspaceList workspaces={[]} onRemove={vi.fn()} onAdd={onAdd} />)

    fireEvent.click(screen.getByRole('button', { name: '添加工作区' }))

    expect(onAdd).toHaveBeenCalledTimes(1)
  })
})
