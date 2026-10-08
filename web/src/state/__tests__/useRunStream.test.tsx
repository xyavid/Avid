// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { TimelineItem } from '../timeline'
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

type Frame = { type: string; ts: number; seq: number | null; data: Record<string, unknown> }

function emitter(): { send: (type: string, data: Record<string, unknown>, seq?: number, ts?: number) => void } {
  const handlers = subscribe.mock.calls.at(-1)?.[2] as { onEvent: (e: Frame) => void }
  return {
    send: (type, data, seq = 0, ts = 0) => handlers.onEvent({ type, ts, seq: seq ?? null, data }),
  }
}

/** 时间线上的正文与用户段，按顺序取出来断言。 */
function texts(items: TimelineItem[], kind: 'assistant' | 'user'): string[] {
  return items.filter((item) => item.kind === kind).map((item) => (item.kind === kind ? item.text : ''))
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

describe('useRunStream（发送 → 订阅 → 段落归并 → 终态收尾）', () => {
  it('send：POST 只带 prompt 与非默认字段；完全访问时附 full_access_ack', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', false)
    })
    expect(startRun).toHaveBeenCalledWith('s1', { prompt: '你好' })
    expect(subscribe).toHaveBeenCalledWith('r1', 0, expect.anything())

    await act(async () => {
      await result.current.send('放开跑', true)
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', {
      prompt: '放开跑',
      full_access_ack: true,
    })

    // 选了模型才带 model；不选时载荷与旧行为逐字一致（服务端按设置解析）
    await act(async () => {
      await result.current.send('换个模型', false, 'deepseek-reasoner')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', {
      prompt: '换个模型',
      model: 'deepseek-reasoner',
    })

    await act(async () => {
      await result.current.send('还是跟随设置', false, null)
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '还是跟随设置' })

    // 分叉之后：只有非 main 才带 branch（main 与旧行为逐字一致）
    await act(async () => {
      await result.current.send('在分支上问', false, null, 'b2')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '在分支上问', branch: 'b2' })

    await act(async () => {
      await result.current.send('在主线上问', false, null, 'main')
    })
    expect(startRun).toHaveBeenLastCalledWith('s1', { prompt: '在主线上问' })
  })

  it('发送先画乐观用户段，user_message 到达后就地收编（不来回多一条）', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', false)
    })
    // 乐观段的 ts 是本地时钟的临时值（发送到事件到达之间），到达后被事件读数取代
    expect(result.current.items).toEqual([
      { kind: 'user', entryId: null, text: '你好', ts: expect.any(Number) },
    ])

    act(() => {
      emitter().send('user_message', { entry_id: 'e1', message: { role: 'user', content: '你好' } }, 0, 7)
    })
    expect(result.current.items).toEqual([{ kind: 'user', entryId: 'e1', text: '你好', ts: 7 }])
  })

  it('run_started 的两值权限口径落到 runPermission；沙箱事实不从这里反推', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('放开跑', true)
    })
    const bus = emitter()

    act(() => {
      bus.send(
        'run_started',
        { permission: 'full', sandbox_state: { policy: 'disabled' }, sandbox_notes: [] },
        1,
      )
    })
    expect(result.current.runPermission).toBe('full')

    act(() => {
      bus.send('run_started', { permission: 'normal', sandbox_state: { policy: 'workspace' } }, 2)
    })
    expect(result.current.runPermission).toBe('normal')
  })

  it('思考与工具按事件流顺序交错成段，相邻思考合并、思考不进正文', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('跑一下', false)
    })
    const { send } = emitter()

    act(() => {
      send('reasoning_delta', { text: '先看 ' }, 0, 1000)
      send('reasoning_delta', { text: '五层状态' }, 0, 1200)
      send('tool_call_started', { tool: 'bash', tool_call_id: 'c1', arguments: { command: 'ls' } }, 2, 1300)
      send('reasoning_delta', { text: '…再想想' }, 0, 1400)
      send('assistant_delta', { text: '结论是这样' }, 0, 1500)
      send('tool_result_message', { message: { role: 'tool', tool_call_id: 'c1', content: 'total 0' } }, 3, 1600)
    })

    expect(result.current.items.map((item) => item.kind)).toEqual([
      'user',
      'reasoning',
      'tool',
      'reasoning',
      'assistant',
    ])
    expect(result.current.items[1]).toMatchObject({
      kind: 'reasoning',
      text: '先看 五层状态',
      startedAt: 1000,
      endedAt: 1200,
    })
    // 结果先落地、终态事件还没来：状态按结果文案定（与重读会话同一口径）
    expect(result.current.items[2]).toMatchObject({
      kind: 'tool',
      callId: 'c1',
      name: 'bash',
      args: '{"command":"ls"}',
      result: 'total 0',
      status: 'ok',
    })
    expect(texts(result.current.items, 'assistant')).toEqual(['结论是这样'])
    expect(texts(result.current.items, 'user')).toEqual(['跑一下'])
    // Dock 进程面板的扁平表随段派生
    expect(result.current.tools).toEqual([
      { callId: 'c1', tool: 'bash', status: 'ok', arguments: '{"command":"ls"}', result: 'total 0' },
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
      await result.current.send('跑', false)
    })
    const bus = emitter()

    act(() => {
      bus.send('assistant_delta', { text: 'a' })
      bus.send('assistant_delta', { text: 'b' })
    })
    // 帧没跑：state 保持原样（这正是合帧的目的一一回渲染不随 token 数增长）
    expect(texts(result.current.items, 'assistant')).toEqual([])
    act(() => {
      frames.splice(0).forEach((cb) => cb(0))
    })
    expect(texts(result.current.items, 'assistant')).toEqual(['ab'])

    act(() => {
      bus.send('assistant_delta', { text: 'c' })
      bus.send('assistant_message', { entry_id: 'e2', message: { role: 'assistant', content: '最终' } }, 5)
    })
    // 最终消息权威：未刷帧的增量作废，不得接在最终文本之后
    expect(texts(result.current.items, 'assistant')).toEqual(['最终'])
  })

  it('同一条会话里连着问：上一条的段落留着（时间线是一条，不因换轮次清空）', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('第一轮', false)
    })
    act(() => {
      emitter().send('reasoning_delta', { text: '上一轮的思考' }, 0, 1000)
    })
    expect(result.current.items).toHaveLength(2)

    await act(async () => {
      await result.current.send('第二轮', false)
    })
    expect(result.current.items.map((item) => item.kind)).toEqual(['user', 'reasoning', 'user'])
    expect(texts(result.current.items, 'user')).toEqual(['第一轮', '第二轮'])
  })

  it('换会话发送：上一条会话的段落不跟过去', async () => {
    const { result, rerender } = renderHook(({ id }) => useRunStream(id, () => {}), {
      initialProps: { id: 's1' },
    })
    await act(async () => {
      await result.current.send('旧会话的问题', false)
    })
    act(() => {
      emitter().send('reasoning_delta', { text: '旧会话的思考' }, 0, 1000)
    })

    rerender({ id: 's2' })
    await act(async () => {
      await result.current.send('新会话的问题', false)
    })

    expect(result.current.items).toEqual([
      { kind: 'user', entryId: null, text: '新会话的问题', ts: expect.any(Number) },
    ])
    expect(result.current.attachedSession).toBe('s2')
  })

  it('父与子的流式增量各自成串：子的正文进它那条子运行，不混进父那段', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('派活', false)
    })
    const subagentArgs = JSON.stringify({ tasks: [{ description: '统计 a.py', prompt: 'x' }] })
    act(() => {
      emitter().send(
        'assistant_message',
        {
          entry_id: 'e2',
          message: {
            role: 'assistant',
            content: '派活。',
            tool_calls: [{ id: 'call_sub', type: 'function', function: { name: 'subagent', arguments: subagentArgs } }],
          },
        },
        0,
        10,
      )
    })
    act(() => {
      // 两条增量交错的到达顺序 = 子先吐了一片、父接着吐
      emitter().send('assistant_delta', { text: '子说', subagent: { task: '统计 a.py', index: 0 } }, 0, 20)
      emitter().send('assistant_delta', { text: '父说' }, 0, 21)
    })

    const texts = (items: TimelineItem[]) =>
      items.flatMap((item) => (item.kind === 'assistant' ? [item.text] : []))

    expect(texts(result.current.items)).toEqual(['派活。', '父说'])
    const card = result.current.items.find((item) => item.kind === 'tool' && item.name === 'subagent')
    if (card?.kind !== 'tool') throw new Error('期望时间线上有那张 subagent 卡')
    expect(texts(card.runs[0]!.items)).toEqual(['子说'])
  })

  it('子运行的事件不改父读数：带 subagent 标记的用量快照不顶替容量环', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('问一句', false)
    })
    const report = (tokens: number) => ({
      context: { tokens, window: 200000, utilization: 0.01, parts: null },
      cache: { read_tokens: null, write_tokens: null, hit_ratio: null },
      compaction: { count: 0, last_compaction_tokens: null, last_step: null },
    })

    act(() => {
      emitter().send('run_status', { round: 1, tokens: 10, usage: report(1234) })
    })
    act(() => {
      // 子运行也会发 run_status（同一个事件名 + subagent 标记）：它的占用各算各的
      emitter().send('run_status', { round: 1, tokens: 10, usage: report(999), subagent: { task: '甲', index: 0 } })
    })

    expect(result.current.usage?.context.tokens).toBe(1234)
  })

  it('活事件：工具行登记与状态迁移、审批入列', async () => {
    const onSettled = vi.fn()
    const { result } = renderHook(() => useRunStream('s1', onSettled))
    await act(async () => {
      await result.current.send('你好', false)
    })
    const bus = emitter()

    await act(async () => {
      bus.send('assistant_delta', { text: '你好' })
      bus.send('assistant_delta', { text: '呀' })
      bus.send('tool_call_started', { tool: 'bash', tool_call_id: 'c1', arguments: {} }, 2)
      bus.send('tool_call_finished', { tool: 'bash', tool_call_id: 'c1', status: 'ok', duration_ms: 40 }, 3)
      bus.send('approval_requested', { approval_id: 'a1', tool: 'bash', arguments: '{}', reason: '递归删除根目录' }, 4)
    })

    expect(texts(result.current.items, 'assistant')).toEqual(['你好呀'])
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

  it('终态：置 settling 并回调 onSettled；settle 只清运行态，段落留着', async () => {
    const onSettled = vi.fn()
    const { result } = renderHook(() => useRunStream('s1', onSettled))
    await act(async () => {
      await result.current.send('你好', false)
    })
    const bus = emitter()

    await act(async () => {
      bus.send('user_message', { entry_id: 'e1', message: { role: 'user', content: '你好' } }, 1)
      bus.send('assistant_message', { entry_id: 'e2', message: { role: 'assistant', content: '在' } }, 2)
      bus.send('run_finished', {}, 9)
    })
    expect(result.current.phase).toBe('settling')
    expect(onSettled).toHaveBeenCalledOnce()

    act(() => {
      result.current.settle()
    })
    expect(result.current.phase).toBe('idle')
    expect(result.current.approvals).toEqual([])
    // 收尾不清段落：清了就等于「过程一个样、最后另起一个样」
    expect(texts(result.current.items, 'assistant')).toEqual(['在'])
    expect(result.current.items.map((item) => item.kind)).toEqual(['user', 'assistant'])
  })

  it('发送失败：phase=error 且给出后端信封文案', async () => {
    startRun.mockRejectedValue(new Error('一个会话同时至多一个运行'))
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', false)
    })
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toContain('一个会话')
  })

  it('stop：调 cancelRun', async () => {
    const { result } = renderHook(() => useRunStream('s1', () => {}))
    await act(async () => {
      await result.current.send('你好', false)
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
      await result.current.send('你好', false)
    })
    const handlers = subscribe.mock.calls.at(-1)?.[2] as { onError: (e: Error) => void }

    await act(async () => {
      handlers.onError(new Error('断线'))
    })
    await waitFor(() => expect(onSettled).toHaveBeenCalled())
    expect(result.current.phase).toBe('settling')
  })
})
