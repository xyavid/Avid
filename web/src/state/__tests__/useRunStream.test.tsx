// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useRunStream } from '../useRunStream'

const startRun = vi.fn()
const cancelRun = vi.fn()
const getRun = vi.fn()
const decideApproval = vi.fn()
const subscribe = vi.fn()

vi.mock('../../api/client', () => ({
  startRun: (...a: unknown[]) => startRun(...a),
  cancelRun: (...a: unknown[]) => cancelRun(...a),
  getRun: (...a: unknown[]) => getRun(...a),
  decideApproval: (...a: unknown[]) => decideApproval(...a),
}))
vi.mock('../../api/events', () => ({
  subscribeRun: (...a: unknown[]) => subscribe(...a),
}))

function emitter(): { send: (type: string, data: Record<string, unknown>, seq?: number | null) => void } {
  const handlers = subscribe.mock.calls.at(-1)?.[2] as {
    onEvent: (e: { type: string; seq: number | null; data: Record<string, unknown> }) => void
  }
  return {
    send: (type, data, seq = null) => handlers.onEvent({ type, seq: seq ?? null, data }),
  }
}

beforeEach(() => {
  // delta 合帧走 rAF；jsdom 没有实现，测试里同步触发（帧内合并不变）
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0)
    return 1
  })
  vi.stubGlobal('cancelAnimationFrame', () => {})
  startRun.mockReset().mockResolvedValue({ run_id: 'r1', session_id: 's1', status: 'running' })
  cancelRun.mockReset().mockResolvedValue({ run_id: 'r1', status: 'running', cancel_requested: true })
  getRun.mockReset()
  decideApproval.mockReset().mockResolvedValue({ accepted: true, decision: 'allow' })
  subscribe.mockReset().mockResolvedValue({ cursor: () => 0, abort: vi.fn() })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useRunStream（发送 → 订阅 → 活事件 → 终态回拉）', () => {
  it('send：POST 带 prompt/permission；full 模式自动附 full_access_ack', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', 'manual')
    })
    expect(startRun).toHaveBeenCalledWith('s1', { prompt: '你好', permission: 'manual' })
    expect(subscribe).toHaveBeenCalledWith('r1', 0, expect.anything())

    await act(async () => {
      await result.current.send('放开跑', 'full')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', {
      prompt: '放开跑',
      permission: 'full',
      full_access_ack: true,
    })

    // 选了模型才带 model；不选时载荷与旧行为逐字一致（服务端按设置解析）
    await act(async () => {
      await result.current.send('换个模型', 'manual', 'deepseek-reasoner')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', {
      prompt: '换个模型',
      permission: 'manual',
      model: 'deepseek-reasoner',
    })

    await act(async () => {
      await result.current.send('还是跟随设置', 'manual', null)
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '还是跟随设置', permission: 'manual' })

    // 分叉之后：只有非 main 才带 branch（main 与旧行为逐字一致）
    await act(async () => {
      await result.current.send('在分支上问', 'manual', null, 'b2')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '在分支上问', permission: 'manual', branch: 'b2' })

    await act(async () => {
      await result.current.send('在主线上问', 'manual', null, 'main')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '在主线上问', permission: 'manual' })
  })

  it('思考与工具按事件流顺序交错成段，相邻思考合并、思考不进正文', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('跑一下', 'manual')
    })
    const { send } = emitter()

    act(() => {
      send('reasoning_delta', { text: '先看 ' })
      send('reasoning_delta', { text: '五层状态' })
      send('tool_call_started', { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'ls' } }, 2)
      send('reasoning_delta', { text: '…再想想' })
      send('assistant_delta', { text: '结论是这样' })
      send('tool_result_message', { message: { role: 'tool', tool_call_id: 'c1', content: 'total 0' } }, 3)
    })

    expect(result.current.segments).toEqual([
      { kind: 'reasoning', text: '先看 五层状态' },
      { kind: 'tool', callId: 'c1', tool: 'bash', status: 'running', arguments: '{"command":"ls"}', result: 'total 0' },
      { kind: 'reasoning', text: '…再想想' },
    ])
    expect(result.current.assistantText).toBe('结论是这样')
    // Dock 进程面板的扁平表随段派生
    expect(result.current.tools).toEqual([
      { callId: 'c1', tool: 'bash', status: 'running', arguments: '{"command":"ls"}', result: 'total 0' },
    ])
  })

  it('delta 合帧：同一帧内多次增量一次刷出；assistant_message 作废未刷的增量', async () => {
    const frames: FrameRequestCallback[] = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
      frames.push(cb)
      return frames.length
    })
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('跑', 'manual')
    })
    const bus = emitter()

    act(() => {
      bus.send('assistant_delta', { text: 'a' })
      bus.send('assistant_delta', { text: 'b' })
    })
    // 帧没跑：state 保持原样（这正是合帧的目的一一回渲染不随 token 数增长）
    expect(result.current.assistantText).toBe('')
    act(() => {
      frames.splice(0).forEach((cb) => cb(0))
    })
    expect(result.current.assistantText).toBe('ab')

    act(() => {
      bus.send('assistant_delta', { text: 'c' })
      bus.send('assistant_message', { message: { role: 'assistant', content: '最终' } }, 5)
    })
    // 最终消息权威：未刷帧的增量作废，不得接在最终文本之后
    expect(result.current.assistantText).toBe('最终')
  })

  it('新一次发送会清掉上一轮的思考（它只属于那一次运行）', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('第一轮', 'manual')
    })
    act(() => emitter().send('reasoning_delta', { text: '上一轮的思考' }))
    expect(result.current.segments).toEqual([{ kind: 'reasoning', text: '上一轮的思考' }])

    await act(async () => {
      await result.current.send('第二轮', 'manual')
    })
    expect(result.current.segments).toEqual([])
  })

  it('活事件：delta 累积、工具行登记与状态迁移、审批入列', async () => {
    const onSettled = vi.fn()
    const { result } = renderHook(() => useRunStream('s1', onSettled))
    await act(async () => {
      await result.current.send('你好', 'auto')
    })
    const bus = emitter()

    await act(async () => {
      bus.send('user_message', { entry_id: 'e1', message: { role: 'user', content: '你好' } }, 1)
      bus.send('assistant_delta', { text: '你好' })
      bus.send('assistant_delta', { text: '呀' })
      bus.send('tool_call_started', { tool: 'bash', tool_call_id: 'c1', arguments: {} }, 2)
      bus.send('tool_call_finished', { tool: 'bash', tool_call_id: 'c1', status: 'ok' }, 3)
      bus.send('approval_requested', { approval_id: 'a1', tool: 'bash', arguments: '{}', reason: '越界' }, 4)
    })

    expect(result.current.assistantText).toBe('你好呀')
    expect(result.current.tools).toEqual([
      { callId: 'c1', tool: 'bash', status: 'ok', arguments: '{}', result: null },
    ])
    expect(result.current.approvals).toHaveLength(1)
    expect(result.current.phase).toBe('running')

    await act(async () => {
      await result.current.decide('a1', 'allow')
    })
    expect(decideApproval).toHaveBeenCalledWith('r1', 'a1', 'allow')
  })

  it('终态：置 settling 并回调 onSettled（由页面回拉后 reset 回 idle）', async () => {
    const onSettled = vi.fn()
    const { result } = renderHook(() => useRunStream('s1', onSettled))
    await act(async () => {
      await result.current.send('你好', 'manual')
    })
    const bus = emitter()

    await act(async () => {
      bus.send('run_finished', {}, 9)
    })
    expect(result.current.phase).toBe('settling')
    expect(onSettled).toHaveBeenCalledOnce()

    act(() => {
      result.current.reset()
    })
    expect(result.current.phase).toBe('idle')
    expect(result.current.assistantText).toBe('')
  })

  it('发送失败：phase=error 且给出后端信封文案', async () => {
    startRun.mockRejectedValue(new Error('一个会话同时至多一个运行'))
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', 'manual')
    })
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toContain('一个会话')
  })

  it('stop：调 cancelRun', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', 'manual')
    })
    await act(async () => {
      await result.current.stop()
    })
    expect(cancelRun).toHaveBeenCalledWith('r1')
  })

  it('流断开：轮询 getRun 到终态后回拉（终端兜底）', async () => {
    getRun.mockResolvedValue({ status: 'finished' })
    const onSettled = vi.fn()
    const { result } = renderHook(() => useRunStream('s1', onSettled))
    await act(async () => {
      await result.current.send('你好', 'manual')
    })
    const handlers = subscribe.mock.calls.at(-1)?.[2] as { onError: (e: Error) => void }

    await act(async () => {
      handlers.onError(new Error('断线'))
    })
    await waitFor(() => expect(onSettled).toHaveBeenCalled())
    expect(result.current.phase).toBe('settling')
  })
})
