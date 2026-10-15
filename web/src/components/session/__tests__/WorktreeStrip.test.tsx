// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { WorktreeFacts } from '../../../api/types'
import { WorktreeStrip } from '../WorktreeStrip'

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

describe('WorktreeStrip（会话那一行）', () => {
  it('有树时显示分支与路径，点一下复制路径', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<WorktreeStrip facts={facts()} />)

    expect(screen.getByText(/avid\/session-s-1/)).toBeTruthy()
    expect(screen.getByText('/home/u/.avid/worktrees/w-1/s-1')).toBeTruthy()
    expect(screen.queryByText(/个提交/)).toBeNull() // the panel's business, not this line's

    fireEvent.click(screen.getByRole('button', { name: '复制工作树路径' }))
    expect(writeText).toHaveBeenCalledWith('/home/u/.avid/worktrees/w-1/s-1')
  })

  it('没有树时不占位置：会话头不该多出一行', () => {
    const { container } = render(<WorktreeStrip facts={facts({ worktree: false, branch: null })} />)
    expect(container.textContent).toBe('')
  })

  it('还没读到时不占位置', () => {
    const { container } = render(<WorktreeStrip facts={null} />)
    expect(container.textContent).toBe('')
  })
})
