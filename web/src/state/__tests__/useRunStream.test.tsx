// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  startRun.mockReset().mockResolvedValue({ run_id: 'r1', session_id: 's1', status: 'running' })
  cancelRun.mockReset().mockResolvedValue({ run_id: 'r1', status: 'running', cancel_requested: true })
  getRun.mockReset()
  decideApproval.mockReset().mockResolvedValue({ accepted: true, decision: 'allow' })
  subscribe.mockReset().mockResolvedValue({ cursor: () => 0, abort: vi.fn() })
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

  it('reasoning_delta 单独累积：思考不进正文，正文也不进思考', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('跑一下', 'manual')
    })
    const { send } = emitter()

    act(() => {
      send('reasoning_delta', { text: '先看 ' })
      send('reasoning_delta', { text: '五层状态' })
      send('assistant_delta', { text: '结论是' })
      send('reasoning_delta', { text: '…再想想' })
      send('assistant_delta', { text: '这样' })
    })

    expect(result.current.reasoning).toBe('先看 五层状态…再想想')
    expect(result.current.assistantText).toBe('结论是这样')
  })

  it('新一次发送会清掉上一轮的思考（它只属于那一次运行）', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('第一轮', 'manual')
    })
    act(() => emitter().send('reasoning_delta', { text: '上一轮的思考' }))
    expect(result.current.reasoning).toBe('上一轮的思考')

    await act(async () => {
      await result.current.send('第二轮', 'manual')
    })
    expect(result.current.reasoning).toBe('')
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
      bus.send('tool_call_started', { tool: 'bash', tool_call_id: 'c1', arguments: '{}' }, 2)
      bus.send('tool_call_finished', { tool: 'bash', tool_call_id: 'c1', status: 'ok' }, 3)
      bus.send('approval_requested', { approval_id: 'a1', tool: 'bash', arguments: '{}', reason: '越界' }, 4)
    })

    expect(result.current.assistantText).toBe('你好呀')
    expect(result.current.tools).toEqual([{ callId: 'c1', tool: 'bash', status: 'ok' }])
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
