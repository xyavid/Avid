// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionSummary } from '../../../api/types'
import { SessionNav } from '../SessionNav'

function session(id: string, name: string, workspaceId: string | null): SessionSummary {
  return {
    id,
    name,
    created_at: 0,
    storage_version: 1,
    parent_session_id: null,
    workspace: workspaceId
      ? { id: workspaceId, root: `/ws/${workspaceId}`, name: workspaceId, default_permission: 'manual' }
      : null,
    message_count: 1,
    active_run_id: null,
    truncated_tail: false,
  }
}

const SESSIONS = [
  session('s1', 'Avid 的会话', 'w1'),
  session('s2', '同项目另一会话', 'w1'),
  session('s3', '别的项目的会话', 'w2'),
]

afterEach(cleanup)

describe('SessionNav（会话按项目管理）', () => {
  it('只显示指定项目（工作区）下的会话', () => {
    render(<SessionNav sessions={SESSIONS} workspaceId="w1" selectedId="s1" onSelect={() => {}} />)

    expect(screen.getByText('Avid 的会话')).toBeTruthy()
    expect(screen.getByText('同项目另一会话')).toBeTruthy()
    expect(screen.queryByText('别的项目的会话')).toBeNull()
  })

  it('项目下没有会话：给空态提示', () => {
    render(<SessionNav sessions={SESSIONS} workspaceId="w-empty" selectedId={null} onSelect={() => {}} />)

    expect(screen.getByText('这个项目还没有会话')).toBeTruthy()
  })

  it('搜索在项目过滤结果内进行', () => {
    render(<SessionNav sessions={SESSIONS} workspaceId="w1" selectedId={null} onSelect={() => {}} />)

    fireEvent.change(screen.getByPlaceholderText('搜索会话…'), { target: { value: '同项目' } })
    expect(screen.getByText('同项目另一会话')).toBeTruthy()
    expect(screen.queryByText('Avid 的会话')).toBeNull()
  })

  it('未指定项目（null）：全部显示（无项目时的兜底形态）', () => {
    render(<SessionNav sessions={SESSIONS} workspaceId={null} selectedId={null} onSelect={() => {}} />)

    expect(screen.getByText('Avid 的会话')).toBeTruthy()
    expect(screen.getByText('别的项目的会话')).toBeTruthy()
  })

  it('点击会话回调 onSelect', () => {
    const onSelect = vi.fn()
    render(<SessionNav sessions={SESSIONS} workspaceId="w1" selectedId="s1" onSelect={onSelect} />)

    fireEvent.click(screen.getByText('Avid 的会话'))
    expect(onSelect).toHaveBeenCalledWith('s1')
  })

  it('标题行「会话」+ 新建：有项目才可点，点了回调 onCreateSession', () => {
    const onCreateSession = vi.fn()
    render(
      <SessionNav
        sessions={SESSIONS}
        workspaceId="w1"
        selectedId={null}
        onSelect={() => {}}
        onCreateSession={onCreateSession}
      />,
    )

    expect(screen.getByText('会话')).toBeTruthy()
    const create = screen.getByRole('button', { name: '新建会话' }) as HTMLButtonElement
    expect(create.disabled).toBe(false)
    fireEvent.click(create)
    expect(onCreateSession).toHaveBeenCalledOnce()
  })

  it('没有选中项目：新建按钮禁用并说明（workspace 是服务端必填项）', () => {
    render(
      <SessionNav sessions={SESSIONS} workspaceId={null} selectedId={null} onSelect={() => {}} onCreateSession={() => {}} />,
    )

    const create = screen.getByRole('button', { name: '新建会话' }) as HTMLButtonElement
    expect(create.disabled).toBe(true)
    expect(create.title).toContain('项目')
  })

  it('动作透传：重命名给 (id, 新名字)，删除给 (id)', () => {
    const onRenameSession = vi.fn()
    const onDeleteSession = vi.fn()
    render(
      <SessionNav
        sessions={SESSIONS}
        workspaceId="w1"
        selectedId="s1"
        onSelect={() => {}}
        onRenameSession={onRenameSession}
        onDeleteSession={onDeleteSession}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '重命名：Avid 的会话' }))
    const input = screen.getByLabelText('会话名称')
    fireEvent.change(input, { target: { value: '改名后的会话' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onRenameSession).toHaveBeenCalledWith('s1', '改名后的会话')

    fireEvent.click(screen.getByRole('button', { name: '删除：同项目另一会话' }))
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onDeleteSession).toHaveBeenCalledWith('s2')
  })

  it('notice：动作结果与失败原因各是一句人话，挂在列表下方', () => {
    render(
      <SessionNav
        sessions={SESSIONS}
        workspaceId="w1"
        selectedId={null}
        onSelect={() => {}}
        notice="有活动 run 的会话不能删除"
      />,
    )

    expect(screen.getByText('有活动 run 的会话不能删除')).toBeTruthy()
  })
})
