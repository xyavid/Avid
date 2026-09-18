/**
 * B14 / C10：SSE 解析、半行缓冲、解析失败 → resync、退避重连 → degraded。
 *
 * Node 24 自带 Response / ReadableStream，所以不用起服务器、不用 mock undici：
 * 注入一个返回 `Response` 的 `fetchImpl` 就够了。时间也一样——`sleepImpl` 注入成
 * 立即 resolve，退避序列直接用产出的 `delayMs` 断言。
 */

import { describe, expect, it } from 'vitest'

import {
  BACKOFF_MAX_MS,
  BACKOFF_MIN_MS,
  backoffDelay,
  decodeFrame,
  parseSseChunk,
  subscribeRun,
} from '../stream'
import type { StreamSignal } from '../stream'

const encoder = new TextEncoder()

function responseFrom(chunks: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

function asFetch(impl: () => Promise<Response>): typeof fetch {
  return impl as unknown as typeof fetch
}

const noSleep = () => Promise.resolve()

describe('parseSseChunk：半行缓冲', () => {
  it('一条帧从 data 中间切开分两次喂，不丢帧', () => {
    const frame = 'id: 7\nevent: assistant_delta\ndata: {"type":"assistant_delta","text":"你好"}\n\n'
    const dataStart = frame.indexOf('data: ') + 'data: '.length
    const cut = dataStart + 12

    const first = parseSseChunk(frame.slice(0, cut))
    expect(first.frames).toHaveLength(0)
    expect(first.rest).toBe(frame.slice(0, cut))

    const second = parseSseChunk(first.rest + frame.slice(cut))
    expect(second.frames).toHaveLength(1)
    expect(second.frames[0]?.id).toBe(7)
    expect(second.frames[0]?.event).toBe('assistant_delta')
    expect(second.frames[0]?.data).toBe('{"type":"assistant_delta","text":"你好"}')
    expect(second.rest).toBe('')
  })

  it('一次喂多帧时全部解出，尾巴留在 rest 里', () => {
    const { frames, rest } = parseSseChunk('data: a\n\ndata: b\n\ndata: partial')
    expect(frames.map((frame) => frame.data)).toEqual(['a', 'b'])
    expect(rest).toBe('data: partial')
  })
})

describe('decodeFrame', () => {
  it('非法 JSON 抛错（不能静默跳过）', () => {
    expect(() => decodeFrame({ id: null, event: null, data: '{不是 JSON' })).toThrow()
  })

  it('缺少 type 的 JSON 也抛错', () => {
    expect(() => decodeFrame({ id: null, event: null, data: '{"foo":1}' })).toThrow()
  })

  it('注释 / 心跳帧（没有 data）返回 null', () => {
    expect(decodeFrame({ id: null, event: null, data: '' })).toBeNull()
    const heartbeat = parseSseChunk(': 心跳\n\n')
    expect(heartbeat.frames).toHaveLength(1)
    expect(decodeFrame(heartbeat.frames[0]!)).toBeNull()
  })
})

describe('subscribeRun', () => {
  it('解析失败时产出 resync，而不是悄悄丢帧', async () => {
    const fetchImpl = asFetch(async () => responseFrom(['data: {不是合法 JSON}\n\n']))
    const controller = new AbortController()
    const stream = subscribeRun('run-1', {
      signal: controller.signal,
      fetchImpl,
      sleepImpl: noSleep,
    })

    const first = await stream.next()
    expect(first.done).toBe(false)
    expect(first.value).toMatchObject({ kind: 'resync', after: 0 })

    controller.abort()
    await stream.return(undefined)
  })

  it('反复失败时按 1s→30s 退避重连，尝试次数有上界，最后 degraded', async () => {
    const failing = asFetch(async () => {
      throw new Error('boom')
    })
    const controller = new AbortController()
    const signals: StreamSignal[] = []

    const stream = subscribeRun('run-1', {
      signal: controller.signal,
      fetchImpl: failing,
      sleepImpl: noSleep,
      random: () => 0.5, // 抖动系数归零，退避序列可精确断言
      maxAttempts: 3,
    })
    for await (const signal of stream) signals.push(signal)

    const reconnects = signals.filter(
      (signal): signal is Extract<StreamSignal, { kind: 'reconnect' }> => signal.kind === 'reconnect',
    )
    expect(reconnects).toHaveLength(3)
    expect(reconnects.length).toBeLessThanOrEqual(3) // 尝试次数有上界

    for (const signal of reconnects) {
      expect(signal.delayMs).toBeGreaterThanOrEqual(BACKOFF_MIN_MS * 0.7)
      expect(signal.delayMs).toBeLessThanOrEqual(BACKOFF_MAX_MS * 1.3)
    }
    expect(reconnects.map((signal) => signal.delayMs)).toEqual([1_000, 2_000, 4_000])
    expect(signals.at(-1)).toMatchObject({ kind: 'degraded' })
  })

  it('收到终态事件后结束（不再重连）', async () => {
    const terminal = JSON.stringify({
      run_id: 'run-1',
      session_id: 'session-1',
      seq: 1,
      ts: 1,
      type: 'run_finished',
      data: {},
    })
    const fetchImpl = asFetch(async () => responseFrom([`data: ${terminal}\n\n`]))
    const controller = new AbortController()

    const signals: StreamSignal[] = []
    const stream = subscribeRun('run-1', {
      signal: controller.signal,
      fetchImpl,
      sleepImpl: noSleep,
    })
    for await (const signal of stream) signals.push(signal)

    expect(signals).toHaveLength(1)
    expect(signals[0]).toMatchObject({ kind: 'event' })
  })
})

describe('backoffDelay', () => {
  it('单调不减、30s 封顶，抖动归零时可精确断言', () => {
    const flat = () => 0.5
    const series = Array.from({ length: 8 }, (_, index) => backoffDelay(index + 1, flat))

    expect(series).toEqual([1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000])
    for (let i = 1; i < series.length; i += 1) {
      expect(series[i]!).toBeGreaterThanOrEqual(series[i - 1]!)
    }
    expect(Math.max(...series)).toBe(BACKOFF_MAX_MS)

    // 下限：-30% 抖动会被 1s 夹住；上限：+30% 抖动也不会越过 30s。
    expect(backoffDelay(1, () => 0)).toBe(BACKOFF_MIN_MS)
    expect(backoffDelay(1, () => 1)).toBe(1_300)
    expect(backoffDelay(20, () => 1)).toBe(BACKOFF_MAX_MS)
  })
})
