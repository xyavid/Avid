/**
 * SSE 的隔离测试：半行缓冲、退避序列、解析失败 → resync、订阅上限 → degraded、终态收尾。
 *
 * 先列失败清单（AGENTS.md §6：隔离测试先列全"它可能怎么坏"），下面每条用例钉住其中一项：
 *   F1 半行缓冲：帧从中间切开分两次喂，不能丢帧也不能提前解出半条
 *   F2 未成帧的尾巴必须留在 rest 里（下一次拼接才有救）
 *   F3 `\r\n` / 孤立 `\r` 归一：Windows 反代可能改写行尾
 *   F4 同一帧多条 `data:` 行按 `\n` 拼接（SSE 规定），不能只留最后一条
 *   F5 `:` 注释行（心跳）忽略，且解出来是空 data
 *   F6 帧非法 JSON 或缺少 type 必须抛错，绝不静默返回半个信封
 *   F7 退避 1s→30s、±30% 抖动、注入 random 后序列可精确断言
 *   F8 游标只走 `Last-Event-ID` 请求头，`after` 没给就不带
 *   F9 delta 默认不订阅：只有 deltas:true 才带 `?deltas=1`
 *   F10 一条帧解析失败 → 产出 `resync` 并重连；重连次数有上界，超过 → `degraded`
 *      （不能像旧实现那样 `continue`，那会对着同一条坏帧无限重连）
 *   F11 终态事件到达后生成器结束，不再重连
 *   F12 外部 abort 之后不发新请求、不产出信号（安静退出）
 */

import { describe, expect, it, vi } from 'vitest'

import {
  BACKOFF_MAX_MS,
  BACKOFF_MIN_MS,
  backoffDelay,
  decodeFrame,
  parseSseChunk,
  subscribeRunEvents,
} from '../stream'
import type { StreamSignal } from '../stream'

const RUN = 'run-1'

const encoder = new TextEncoder()

/** 用 Node 自带的 ReadableStream 造一个 SSE 响应：不用起服务器，也不用 mock undici。 */
function sseResponse(chunks: string[]): Response {
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

/** 退避等待注入成立即 resolve：用例不该真的睡 1 秒。 */
const noSleep = () => Promise.resolve()

/** 造一条真实的服务端帧：`id:` 只在 durable 的 seq 存在时出现（sse.encode 同规则）。 */
function frameOf(
  type = 'run_finished',
  seq: number | null = null,
  data: Record<string, unknown> = {},
): string {
  const envelope = { run_id: RUN, session_id: 's-1', seq, ts: 1_000 + (seq ?? 0), type, data }
  const id = seq === null ? '' : `id: ${seq}\n`
  return `${id}event: ${type}\ndata: ${JSON.stringify(envelope)}\n\n`
}

async function drain(
  options: Parameters<typeof subscribeRunEvents>[1],
): Promise<StreamSignal[]> {
  const signals: StreamSignal[] = []
  for await (const signal of subscribeRunEvents(RUN, options)) signals.push(signal)
  return signals
}

describe('parseSseChunk：半行缓冲', () => {
  it('F1：一条帧从 data 中间切开分两次喂，不丢帧也不提前解出', () => {
    const frame = 'id: 7\nevent: assistant_delta\ndata: {"type":"assistant_delta","text":"你好"}\n\n'
    const dataStart = frame.indexOf('data: ') + 'data: '.length
    const cut = dataStart + 12

    const first = parseSseChunk(frame.slice(0, cut))
    expect(first.frames).toHaveLength(0)
    expect(first.rest).toBe(frame.slice(0, cut))

    const second = parseSseChunk(first.rest + frame.slice(cut))
    expect(second.frames).toHaveLength(1)
    expect(second.frames[0]).toEqual({
      id: 7,
      event: 'assistant_delta',
      data: '{"type":"assistant_delta","text":"你好"}',
    })
    expect(second.rest).toBe('')
  })

  it('F2：多帧一次喂完，未成帧的尾巴留在 rest 里', () => {
    const { frames, rest } = parseSseChunk('data: a\n\ndata: b\n\ndata: partial')
    expect(frames.map((frame) => frame.data)).toEqual(['a', 'b'])
    expect(rest).toBe('data: partial')
  })

  it('F3：\\r\\n 与孤立 \\r 都归一成 \\n', () => {
    const crlf = parseSseChunk('id: 3\r\nevent: run_status\r\ndata: {"a":1}\r\n\r\n')
    expect(crlf.frames).toHaveLength(1)
    expect(crlf.frames[0]).toEqual({ id: 3, event: 'run_status', data: '{"a":1}' })

    const loneCr = parseSseChunk('data: x\r\rdata: y\r\r')
    expect(loneCr.frames.map((frame) => frame.data)).toEqual(['x', 'y'])
  })

  it('F4：同一帧多条 data 行按 \\n 拼接（SSE 规定，不能只留最后一条）', () => {
    const { frames } = parseSseChunk('data: 第一行\ndata: 第二行\n\n')
    expect(frames[0]?.data).toBe('第一行\n第二行')
  })

  it('F5：注释行忽略，无 data 的帧解出空 data（心跳走这条）', () => {
    const { frames } = parseSseChunk(': ping\n\n')
    expect(frames).toEqual([{ id: null, event: null, data: '' }])
  })

  it('空块（帧之间多余的换行）不产出假帧', () => {
    const { frames } = parseSseChunk('\n\ndata: a\n\n\n\n')
    expect(frames.map((frame) => frame.data)).toEqual(['a'])
  })
})

describe('decodeFrame', () => {
  const envelope = {
    run_id: RUN,
    session_id: 's-1',
    seq: 4,
    ts: 100,
    type: 'run_finished',
    data: {},
  }

  it('F6：data 是合法 JSON 且带 type → 原样解出', () => {
    expect(decodeFrame({ id: 4, event: 'run_finished', data: JSON.stringify(envelope) })).toEqual(
      envelope,
    )
  })

  it('F6：非法 JSON 抛错（不能静默跳过）', () => {
    expect(() => decodeFrame({ id: null, event: null, data: '{不是 JSON' })).toThrow()
  })

  it('F6：JSON 合法但缺 type 也抛错', () => {
    expect(() => decodeFrame({ id: null, event: null, data: '{"foo":1}' })).toThrow()
  })

  it('F5：没有 data（注释/心跳帧）返回 null', () => {
    expect(decodeFrame({ id: null, event: null, data: '' })).toBeNull()
    const heartbeat = parseSseChunk(': ping\n\n')
    expect(decodeFrame(heartbeat.frames[0]!)).toBeNull()
  })
})

describe('backoffDelay', () => {
  it('F7：抖动归零时序列精确为 1s 起步、30s 封顶', () => {
    const flat = () => 0.5
    const series = Array.from({ length: 8 }, (_, index) => backoffDelay(index + 1, flat))

    expect(series).toEqual([1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000])
    for (let i = 1; i < series.length; i += 1) {
      expect(series[i]!).toBeGreaterThanOrEqual(series[i - 1]!)
    }
  })

  it('F7：±30% 抖动不越过上下界', () => {
    expect(backoffDelay(1, () => 0)).toBe(BACKOFF_MIN_MS)
    expect(backoffDelay(1, () => 1)).toBe(1_300)
    expect(backoffDelay(1, () => 0.5)).toBe(1_000)
    expect(backoffDelay(20, () => 1)).toBe(BACKOFF_MAX_MS)
    expect(backoffDelay(0, () => 0.5)).toBe(BACKOFF_MIN_MS)
  })
})

describe('subscribeRunEvents', () => {
  it('F8：游标走 Last-Event-ID 请求头；after 没给就不带', async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = []
    const fetchImpl = (async (input: unknown, init?: RequestInit) => {
      calls.push({ url: String(input), init })
      return sseResponse([frameOf('run_finished', 7)])
    }) as unknown as typeof fetch

    const controller = new AbortController()
    await drain({ signal: controller.signal, fetchImpl, sleepImpl: noSleep })

    expect(calls).toHaveLength(1)
    expect(calls[0]!.url).not.toContain('after')
    expect(new Headers(calls[0]!.init?.headers).get('Last-Event-ID')).toBeNull()

    const withCursor = new AbortController()
    await drain({ signal: withCursor.signal, after: 3, fetchImpl, sleepImpl: noSleep })

    expect(calls).toHaveLength(2)
    expect(calls[1]!.url).not.toContain('after=')
    expect(new Headers(calls[1]!.init?.headers).get('Last-Event-ID')).toBe('3')
  })

  it('F9：只有 deltas:true 才带 ?deltas=1', async () => {
    const urls: string[] = []
    const fetchImpl = (async (input: unknown) => {
      urls.push(String(input))
      return sseResponse([frameOf('run_finished', 1)])
    }) as unknown as typeof fetch

    for (const deltas of [false, true]) {
      const controller = new AbortController()
      await drain({ signal: controller.signal, fetchImpl, sleepImpl: noSleep, deltas })
    }

    expect(urls[0]).toContain('/runs/run-1/events')
    expect(urls[0], '默认不订阅 delta').not.toContain('deltas')
    expect(urls[1], '显式订阅才带上 deltas=1').toContain('deltas=1')
  })

  it('F10：帧解析失败产出 resync 并重连，尝试次数封顶后 degraded，不死循环', async () => {
    let requests = 0
    const fetchImpl = (async () => {
      requests += 1
      return sseResponse(['data: {不是合法 JSON}\n\n'])
    }) as unknown as typeof fetch

    const controller = new AbortController()
    const signals = await drain({
      signal: controller.signal,
      fetchImpl,
      sleepImpl: noSleep,
      random: () => 0.5,
      maxAttempts: 2,
    })

    expect(signals[0]).toMatchObject({ kind: 'resync', after: 0 })
    expect(signals.at(-1)).toMatchObject({ kind: 'degraded' })
    const reconnects = signals.filter((signal) => signal.kind === 'reconnect')
    expect(reconnects).toHaveLength(2)
    // degraded 之后不再发请求：生成器必须真的结束。
    expect(requests).toBe(3)
  })

  it('F10：连接反复失败时按退避重连，超过 maxAttempts → degraded', async () => {
    const failing = (async () => {
      throw new Error('boom')
    }) as unknown as typeof fetch

    const controller = new AbortController()
    const signals = await drain({
      signal: controller.signal,
      fetchImpl: failing,
      sleepImpl: noSleep,
      random: () => 0.5,
      maxAttempts: 3,
    })

    const delays = signals
      .filter(
        (signal): signal is Extract<StreamSignal, { kind: 'reconnect' }> =>
          signal.kind === 'reconnect',
      )
      .map((signal) => signal.delayMs)

    expect(delays).toEqual([1_000, 2_000, 4_000])
    expect(signals.at(-1)).toMatchObject({ kind: 'degraded' })
  })

  it('F11：收到终态事件后结束，不再重连', async () => {
    const fetchImpl = (async () => sseResponse([frameOf('run_finished', 1)])) as unknown as typeof fetch

    const controller = new AbortController()
    const signals = await drain({ signal: controller.signal, fetchImpl, sleepImpl: noSleep })

    expect(signals).toHaveLength(1)
    const first = signals[0]
    expect(first?.kind).toBe('event')
    expect(first?.kind === 'event' ? first.event.type : null).toBe('run_finished')
  })

  it('F12：已经 abort 的信号不发请求，生成器安静结束', async () => {
    const fetchImpl = vi.fn(async () => sseResponse([frameOf('run_finished', 1)])) as unknown as typeof fetch
    const controller = new AbortController()
    controller.abort()

    const signals = await drain({ signal: controller.signal, fetchImpl, sleepImpl: noSleep })

    expect(signals).toEqual([])
    expect(fetchImpl).not.toHaveBeenCalled()
  })
})
