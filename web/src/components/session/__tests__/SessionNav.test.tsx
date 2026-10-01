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
})
