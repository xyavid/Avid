// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MessageActions } from '../MessageActions'

/**
 * Hover reveals the action row. User messages have no branch: the fork point is the model's
 * reply, not the user's own words.
 */
describe('消息动作行', () => {
  afterEach(cleanup)

  it('复制：把原文写进剪贴板并回执「已复制」', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<MessageActions text="结论是 `avid`" />)

    const copy = screen.getByRole('button', { name: '复制' })
    fireEvent.click(copy)

    expect(writeText).toHaveBeenCalledWith('结论是 `avid`')
    return vi.waitFor(() => expect(screen.getByRole('button', { name: '已复制' })).toBeTruthy())
  })

  it('默认不可见，悬停/聚焦才现形', () => {
    render(<MessageActions text="x" />)

    const row = screen.getByTestId('message-actions')
    expect(row.className).toContain('opacity-0')
    expect(row.className).toContain('group-hover:opacity-100')
    expect(row.className).toContain('group-focus-within:opacity-100')
  })

  it('没有 onBranch 时只有复制（用户消息就是这种：分叉点不是用户自己的话）', () => {
    render(<MessageActions text="帮我读一下" />)

    expect(screen.getByRole('button', { name: '复制' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '分支' })).toBeNull()
  })

  it('给了 onBranch：出现分支按钮，按下带上分叉点', () => {
    const onBranch = vi.fn()
    render(<MessageActions text="结论" onBranch={onBranch} />)

    fireEvent.click(screen.getByRole('button', { name: '分支' }))

    expect(onBranch).toHaveBeenCalledTimes(1)
  })
})
