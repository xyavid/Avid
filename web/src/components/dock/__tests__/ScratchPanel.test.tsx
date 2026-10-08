// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ScratchPanel } from '../ScratchPanel'

const createScratchSession = vi.fn()
const deleteSession = vi.fn()
const cancelRun = vi.fn()
const listEntries = vi.fn()
const startRun = vi.fn()
const subscribeRun = vi.fn()

// vi.mock 的工厂会被提升到文件顶部，类定义必须跟着一起提升（vi.hoisted）。
const { FakeApiError } = vi.hoisted(() => {
  class FakeApiError extends Error {
    readonly code = 'session_not_found'
    readonly status = 404
    readonly detail = null
  }
  return { FakeApiError }
})

vi.mock('../../../api/client', () => ({
  ApiError: FakeApiError,
  createScratchSession: (...args: unknown[]) => createScratchSession(...args),
  deleteSession: (...args: unknown[]) => deleteSession(...args),
  cancelRun: (...args: unknown[]) => cancelRun(...args),
  listEntries: (...args: unknown[]) => listEntries(...args),
  startRun: (...args: unknown[]) => startRun(...args),
  getRun: vi.fn(),
  decideApproval: vi.fn(),
}))
vi.mock('../../../api/events', () => ({
  subscribeRun: (...args: unknown[]) => subscribeRun(...args),
}))

function entry(seq: number, message: Record<string, unknown>) {
  return { entry_id: `e${seq}`, parent_id: null, seq, timestamp: seq, type: 'message', message }
}

beforeEach(() => {
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  createScratchSession.mockReset().mockResolvedValue({
    id: 's-scratch',
    copied_messages: 2,
  })
  deleteSession.mockReset().mockResolvedValue(undefined)
  cancelRun.mockReset().mockResolvedValue({})
  listEntries.mockReset().mockResolvedValue({
    entries: [
      entry(2, { role: 'assistant', content: '主线的回答' }),
      entry(1, { role: 'user', content: '主线的问题' }),
    ],
    has_more: false,
    next_cursor: null,
  })
  startRun.mockReset().mockResolvedValue({ run_id: 'r1', session_id: 's-scratch', status: 'running' })
  subscribeRun.mockReset().mockResolvedValue({ cursor: () => 0, abort: vi.fn() })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('临时对话面板', () => {
  it('挂上就建临时会话：拷来的上下文画出来；三条事实只在 title 里，不占版面', async () => {
    render(<ScratchPanel sourceSessionId="s-main" workspaceRoot="/w" />)

    await waitFor(() => expect(createScratchSession).toHaveBeenCalledWith('s-main'))
    expect(await screen.findByText('主线的问题')).toBeTruthy()
    expect(screen.getByText('主线的回答')).toBeTruthy()

    const title = screen.getByTestId('scratch-panel').getAttribute('title') ?? ''
    expect(title).toContain('2 条上下文')
    expect(title).toContain('只读')
    expect(title).toContain('离开这个面板即删除')
    // 版面里不该出现这些说明句
    expect(screen.queryByText(/已带上主对话的/)).toBeNull()
  })

  it('在里面正常对话：Enter 发一句话，运行落在同一个会话上', async () => {
    render(<ScratchPanel sourceSessionId="s-main" workspaceRoot="/w" />)
    const box = await screen.findByLabelText('临时对话输入')

    fireEvent.change(box, { target: { value: '这里问一句' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    await waitFor(() =>
      expect(startRun).toHaveBeenCalledWith('s-scratch', expect.objectContaining({ prompt: '这里问一句' })),
    )
    // 换行是 Shift+Enter：那一下不该发出去
    startRun.mockClear()
    fireEvent.change(box, { target: { value: '换行' } })
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true })
    expect(startRun).not.toHaveBeenCalled()
  })

  it('卸下就拆：会话被删掉（不是藏起来）', async () => {
    const { unmount } = render(<ScratchPanel sourceSessionId="s-main" workspaceRoot="/w" />)
    await screen.findByText('主线的问题')

    unmount()

    await waitFor(() => expect(deleteSession).toHaveBeenCalledWith('s-scratch'))
  })

  it('建会话失败：把服务端的话原样说出来，输入框不再可用', async () => {
    createScratchSession.mockRejectedValue(new FakeApiError('没有这个会话：s-main'))

    render(<ScratchPanel sourceSessionId="s-main" workspaceRoot="/w" />)

    expect(await screen.findByText('没有这个会话：s-main')).toBeTruthy()
    expect(screen.getByLabelText('临时对话输入').hasAttribute('disabled')).toBe(true)
    expect(listEntries).not.toHaveBeenCalled()
  })
})
