/**
 * 事件流：SSE 取流、`Last-Event-ID` 游标、退避重连、半行缓冲、resync。
 *
 * 四条不许省的东西（`docs/guide/web-ui.md` §2/§3）：
 *   1. **解析失败不静默丢弃**：帧缓冲留着半条，一条帧的 `data` 解析失败就产出 `resync`
 *      信号并重连，而不是跳过它（跳过等于静默少一段历史）。
 *   2. **重连不依赖浏览器默认行为**：自己做退避（1s→30s，±30% 抖动）与游标。
 *      浏览器内置的 EventSource 不能带自定义头，也不能把解析失败变成信号。
 *   3. **反复失败要能降级**：超过尝试上限就 `degraded`，由上层切轮询对账。
 *   4. **连接建立有超时，流本身不设总超时**：一次运行的流可以持续几十分钟，
 *      给整条流设总超时会在长运行里必然掐断。
 */

import { API_BASE } from './client'
import { TERMINAL_EVENT_TYPES } from '../events/types'
import type { EventEnvelope } from '../events/types'

export interface SseFrame {
  id: number | null
  event: string | null
  data: string
}

export interface ParseResult {
  frames: SseFrame[]
  rest: string
}

/**
 * 半行缓冲：按空行（`\n\n`）分帧，缓冲里永远留着还没成帧的尾巴。
 *
 * `\r\n` 与孤立 `\r` 都归一成 `\n`：SSE 规范里三种行尾都合法，反代也可能改写行尾。
 * 同一帧的多条 `data:` 行按 `\n` 拼接（这是 SSE 规定的语义，不是"最后一条胜出"）。
 */
export function parseSseChunk(buffer: string): ParseResult {
  const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const parts = normalized.split('\n\n')
  const rest = parts.pop() ?? ''
  const frames: SseFrame[] = []

  for (const block of parts) {
    // 空块（例如帧之间多余的换行）不是帧：不产出一条空事件。
    if (!block.trim()) continue
    let id: number | null = null
    let event: string | null = null
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      // `:` 开头是注释（服务端的心跳 `: ping` 走这条）。
      if (line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator === -1 ? line : line.slice(0, separator)
      const raw = separator === -1 ? '' : line.slice(separator + 1)
      // 冒号后可选一个前导空格，属于分隔符而不是内容。
      const value = raw.startsWith(' ') ? raw.slice(1) : raw
      if (field === 'id') {
        const parsed = Number.parseInt(value, 10)
        id = Number.isFinite(parsed) ? parsed : null
      } else if (field === 'event') {
        event = value
      } else if (field === 'data') {
        dataLines.push(value)
      }
    }
    frames.push({ id, event, data: dataLines.join('\n') })
  }

  return { frames, rest }
}

/**
 * 一条帧 → 信封。
 *
 * 注释 / 心跳（没有 `data`）返回 `null`；非法 JSON 或缺少 `type` 一律抛出——
 * 抛出是调用方转成 `resync` 信号的前提，**绝不能**在这里静默返回 `null`。
 */
export function decodeFrame(frame: SseFrame): EventEnvelope | null {
  if (!frame.data) return null
  const payload = JSON.parse(frame.data) as EventEnvelope
  if (typeof payload?.type !== 'string') {
    throw new Error('事件帧缺少 type')
  }
  return payload
}

export const BACKOFF_MIN_MS = 1_000
export const BACKOFF_MAX_MS = 30_000
export const BACKOFF_JITTER = 0.3
/** 连接建立（拿到响应头）的超时；拿到之后就交给流自己，不设总超时。 */
export const CONNECT_TIMEOUT_MS = 10_000
export const DEFAULT_MAX_ATTEMPTS = 5

/**
 * 1s→30s 指数退避，±30% 抖动。
 *
 * 抽成纯函数是为了让用例能用固定 `random` 精确断言序列；抖动用来避免多个标签页
 * 在同一刻一起回来（惊群）。
 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MIN_MS * 2 ** Math.max(0, attempt - 1), BACKOFF_MAX_MS)
  const factor = 1 + (random() * 2 - 1) * BACKOFF_JITTER
  return Math.max(BACKOFF_MIN_MS, Math.min(BACKOFF_MAX_MS, Math.round(base * factor)))
}

export type StreamSignal =
  | { kind: 'event'; event: EventEnvelope }
  /** 收到无法解释的帧：上层应重新拉条目补齐（真正生效由状态层决定）。 */
  | { kind: 'resync'; reason: string; after: number }
  | { kind: 'reconnect'; attempt: number; delayMs: number }
  /** 连续失败超过上限：上层切轮询对账，不再占用一条 SSE 连接。 */
  | { kind: 'degraded'; reason: string }

export interface SubscribeOptions {
  /** 已知的最大 durable seq（`Last-Event-ID` 的等价物）；给了才带请求头。 */
  after?: number
  /** delta 默认不投递：显式订阅才带上 `?deltas=1`。 */
  deltas?: boolean
  signal: AbortSignal
  maxAttempts?: number
  fetchImpl?: typeof fetch
  sleepImpl?: (ms: number, signal: AbortSignal) => Promise<void>
  random?: () => number
}

function reasonOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new Error('aborted'))
      return
    }
    const onAbort = () => {
      clearTimeout(timer)
      reject(new Error('aborted'))
    }
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * 流地址。
 *
 * 游标**不走 query**：`Last-Event-ID` 是 SSE 的标准续传头，服务端按它决定从哪个 seq
 * 开始重放；把游标拼进 query 会让"重连"与"新订阅"变成两条不同的语义。
 */
function streamUrl(runId: string, deltas: boolean): string {
  const base = `${API_BASE}/runs/${encodeURIComponent(runId)}/events`
  return deltas ? `${base}?deltas=1` : base
}

function requestHeaders(cursor: number | null): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  // 没有游标（第一次订阅）就不带：服务端从缓冲区起点重放。
  if (cursor !== null) headers['Last-Event-ID'] = String(cursor)
  return headers
}

/**
 * 订阅一个 run 的事件流。生成器只在三种情况下结束：收到终态事件、被 `signal` 取消、
 * 或连续失败超过 `maxAttempts`（先产出 `degraded`）。
 *
 * 退避的重置条件是**有事件成功解出**，不是"连接建立成功"：坏帧 / 服务端立刻关流
 * 也属于失败，否则会对着同一条坏帧无限重连。
 */
export async function* subscribeRunEvents(
  runId: string,
  options: SubscribeOptions,
): AsyncGenerator<StreamSignal> {
  const {
    after,
    deltas = false,
    signal,
    maxAttempts = DEFAULT_MAX_ATTEMPTS,
    fetchImpl = fetch,
    sleepImpl = defaultSleep,
    random = Math.random,
  } = options

  let cursor: number | null = after ?? null
  let attempt = 0

  while (!signal.aborted) {
    // 每次连接一个独立 controller：连接建立超时只掐这一次连接，不掐整条订阅。
    const connect = new AbortController()
    let connectTimedOut = false
    const connectTimer = setTimeout(() => {
      connectTimedOut = true
      connect.abort()
    }, CONNECT_TIMEOUT_MS)
    const onOuterAbort = () => connect.abort()
    signal.addEventListener('abort', onOuterAbort, { once: true })

    try {
      const response = await fetchImpl(streamUrl(runId, deltas), {
        headers: requestHeaders(cursor),
        signal: connect.signal,
      })
      clearTimeout(connectTimer)
      if (signal.aborted) return
      if (!response.ok || !response.body) {
        // 503 `too_many_streams` 也走这里：退避后再来，而不是把 REST 请求一起拖慢。
        throw new Error(`事件流不可用：HTTP ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let sawTerminal = false
      let resyncReason: string | null = null

      while (!signal.aborted) {
        const chunk = await reader.read()
        if (chunk.done) break
        buffer += decoder.decode(chunk.value, { stream: true })
        const parsed = parseSseChunk(buffer)
        buffer = parsed.rest

        for (const frame of parsed.frames) {
          let event: EventEnvelope | null = null
          try {
            event = decodeFrame(frame)
          } catch (error) {
            // 不静默跳过：把"这一段历史看不懂"变成显式信号，并当作一次失败。
            resyncReason = reasonOf(error)
            break
          }
          if (!event) continue

          // 服务端可能从游标处重发已消费的 durable 帧：按 (run_id, seq) 幂等跳过。
          if (event.seq !== null) {
            if (cursor !== null && event.seq <= cursor) continue
            cursor = event.seq
          }
          // 有事件成功解出才算"有进展"，退避从这里重置。
          attempt = 0
          yield { kind: 'event', event }
          if (TERMINAL_EVENT_TYPES.includes(event.type)) {
            sawTerminal = true
            break
          }
        }

        if (resyncReason !== null || sawTerminal) break
      }

      if (sawTerminal) return
      if (resyncReason !== null) {
        yield { kind: 'resync', reason: resyncReason, after: cursor ?? 0 }
        throw new Error(`事件帧无法解析：${resyncReason}`)
      }
      // 服务端关了流却没给终态：权威终止在运行注册表，由上层对账（I13）；
      // 这里先按"断在半路"重连补齐。
      throw new Error('事件流提前结束')
    } catch (error) {
      if (signal.aborted) return
      attempt += 1
      const reason = connectTimedOut ? `连接建立超时（${CONNECT_TIMEOUT_MS}ms）` : reasonOf(error)
      if (attempt > maxAttempts) {
        yield { kind: 'degraded', reason }
        return
      }
      const delayMs = backoffDelay(attempt, random)
      yield { kind: 'reconnect', attempt, delayMs }
      try {
        await sleepImpl(delayMs, signal)
      } catch {
        // 退避期间被取消：abort 不是错误，安静退出。
        return
      }
    } finally {
      clearTimeout(connectTimer)
      signal.removeEventListener('abort', onOuterAbort)
    }
  }
}
