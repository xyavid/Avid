// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorktreeFacts } from '../../../api/types'
import { WorktreePanel } from '../WorktreePanel'

afterEach(cleanup)

function facts(overrides: Partial<WorktreeFacts> = {}): WorktreeFacts {
  return {
    worktree: true,
    path: '/home/u/.avid/worktrees/w-1/s-1',
    branch: 'avid/session-s-1',
    base: 'abc1234',
    reason: null,
    dirty: [],
    commits: [],
    ahead: 0,
    note: null,
    ...overrides,
  }
}

describe('WorktreePanel（这棵树的改动与提交）', () => {
  it('有树：说清在哪、改了什么、提交了什么，并给出合并命令', () => {
    render(
      <WorktreePanel
        facts={facts({
          dirty: [{ path: 'src/a.py', status: ' M' }],
          commits: [{ sha: 'def5678', subject: '会话做的', committed_at: 0 }],
          ahead: 1,
        })}
        loading={false}
        error={null}
        workspaceRoot="/home/u/proj"
      />,
    )

    expect(screen.getByText('分支 avid/session-s-1')).toBeTruthy()
    expect(screen.getByText('/home/u/.avid/worktrees/w-1/s-1')).toBeTruthy()
    expect(screen.getByText('src/a.py')).toBeTruthy()
    expect(screen.getByText('会话做的')).toBeTruthy()
    expect(screen.getByText(/领先主检出 1 个提交/)).toBeTruthy()
    expect(screen.getByText('git -C /home/u/proj merge avid/session-s-1')).toBeTruthy()
  })

  it('没有树：说清原因，不说改动（那是检出自己的事）', () => {
    render(
      <WorktreePanel
        facts={facts({ worktree: false, branch: null, base: null, reason: '工作区不是 git 仓库' })}
        loading={false}
        error={null}
        workspaceRoot="/home/u/proj"
      />,
    )

    expect(screen.getByText(/工作区不是 git 仓库/)).toBeTruthy()
    expect(screen.queryByText(/领先主检出/)).toBeNull()
    expect(screen.queryByText(/merge/)).toBeNull()
  })

  it('树被删了：照 notes 说，不假装干净', () => {
    render(
      <WorktreePanel
        facts={facts({ note: '工作树不在了' })}
        loading={false}
        error={null}
        workspaceRoot="/home/u/proj"
      />,
    )

    expect(screen.getByText(/工作树不在了/)).toBeTruthy()
  })

  it('读不到时说读不到，而不是显示一棵空树', () => {
    render(<WorktreePanel facts={null} loading={false} error="请求失败" workspaceRoot={null} />)

    expect(screen.getByText(/请求失败/)).toBeTruthy()
    expect(screen.queryByText(/领先主检出/)).toBeNull()
  })

  it('路径可复制', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(
      <WorktreePanel facts={facts()} loading={false} error={null} workspaceRoot="/home/u/proj" />,
    )

    fireEvent.click(screen.getByRole('button', { name: '复制工作树路径' }))

    expect(writeText).toHaveBeenCalledWith('/home/u/.avid/worktrees/w-1/s-1')
  })
})
