/**
 * 事件流：SSE 连接、`Last-Event-ID` 游标、退避重连、半行缓冲、resync。
 *
 * 四条不许省的东西（设计文档 §5.4）：
 *   1. **解析失败不静默丢弃**：保留半行缓冲；一条帧的 `data` 解析失败就发 `resync`
 *      信号并重连，而不是跳过（反面样本 Dify 会静默丢事件）。
 *   2. **重连不依赖浏览器默认行为**：自己做退避（1s→30s，±30% 抖动）与游标。
 *   3. **解析失败/连接反复失败要能降级**：超过尝试上限就 yield `degraded`，
 *      由上层切轮询（C12）。
 *   4. **每个网络调用都有超时与 AbortSignal**：连接建立有超时，流本身不设总超时。
 */

import { API_BASE } from './client'
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

/** 半行缓冲：`\n\n` 分帧，缓冲里永远留着没成帧的尾巴。 */
export function parseSseChunk(buffer: string): ParseResult {
  const normalized = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const parts = normalized.split('\n\n')
  const rest = parts.pop() ?? ''
  const frames: SseFrame[] = []

  for (const block of parts) {
    if (!block.trim()) continue
    let id: number | null = null
    let event: string | null = null
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator === -1 ? line : line.slice(0, separator)
      const raw = separator === -1 ? '' : line.slice(separator + 1)
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

/** 一条帧 → 信封。注释/心跳（无 data）返回 null；非法 JSON 抛出。 */
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
export const CONNECT_TIMEOUT_MS = 10_000
export const DEFAULT_MAX_ATTEMPTS = 5

/** 1s→30s 指数退避，±30% 抖动。抽出来是为了让 C10 能断言序列。 */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BACKOFF_MIN_MS * 2 ** Math.max(0, attempt - 1), BACKOFF_MAX_MS)
  const factor = 1 + (random() * 2 - 1) * BACKOFF_JITTER
  return Math.max(BACKOFF_MIN_MS, Math.min(BACKOFF_MAX_MS, Math.round(base * factor)))
}

export type StreamSignal =
  | { kind: 'event'; event: EventEnvelope }
  | { kind: 'resync'; reason: string; after: number }
  | { kind: 'reconnect'; attempt: number; delayMs: number }
  | { kind: 'degraded'; reason: string }

export interface SubscribeOptions {
  /** 已知的最大 durable seq（`Last-Event-ID` 的等价物）。 */
  after?: number
  /** delta 默认不投递：显式订阅才带上 `?deltas=1`。 */
  deltas?: boolean
  signal: AbortSignal
  maxAttempts?: number
  fetchImpl?: typeof fetch
  sleepImpl?: (ms: number, signal: AbortSignal) => Promise<void>
  random?: () => number
}

function defaultSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(new Error('aborted'))
    }
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

function streamUrl(runId: string, after: number, deltas: boolean): string {
  const params = new URLSearchParams({ after: String(after) })
  if (deltas) params.set('deltas', '1')
  return `${API_BASE}/runs/${encodeURIComponent(runId)}/events?${params.toString()}`
}

/**
 * 订阅一个 run 的事件流。生成器只在下列情况结束：收到终态事件、被 AbortSignal
 * 取消、或连续失败超过上限（先 yield `degraded`）。
 */
export async function* subscribeRun(
  runId: string,
  options: SubscribeOptions,
): AsyncGenerator<StreamSignal> {
  const {
    after = 0,
    deltas = false,
    signal,
    maxAttempts = DEFAULT_MAX_ATTEMPTS,
    fetchImpl = fetch,
    sleepImpl = defaultSleep,
    random = Math.random,
  } = options

  let cursor = after
  let attempt = 0

  while (!signal.aborted) {
    const connectTimer = new AbortController()
    const connectTimeout = setTimeout(() => connectTimer.abort(), CONNECT_TIMEOUT_MS)
    const onAbort = () => connectTimer.abort()
    signal.addEventListener('abort', onAbort, { once: true })

    try {
      const response = await fetchImpl(streamUrl(runId, cursor, deltas), {
        headers: { Accept: 'text/event-stream' },
        signal: connectTimer.signal,
      })
      clearTimeout(connectTimeout)

      if (!response.ok || !response.body) {
        throw new Error(`事件流不可用：HTTP ${response.status}`)
      }
      attempt = 0

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let sawTerminal = false
      let needResync = false

      while (!signal.aborted) {
        const chunk = await reader.read()
        if (chunk.done) break
        buffer += decoder.decode(chunk.value, { stream: true })
        const { frames, rest } = parseSseChunk(buffer)
        buffer = rest
        for (const frame of frames) {
          let event: EventEnvelope | null = null
          try {
            event = decodeFrame(frame)
          } catch (error) {
            // 不静默跳过：请求重建视图。
            needResync = true
            yield { kind: 'resync', reason: (error as Error).message, after: cursor }
            break
          }
          if (!event) continue
          if (event.seq !== null && event.seq <= cursor) continue
          if (event.seq !== null) cursor = event.seq
          yield { kind: 'event', event }
          if (
            event.type === 'run_finished' ||
            event.type === 'run_failed' ||
            event.type === 'run_cancelled'
          ) {
            sawTerminal = true
          }
        }
        if (needResync || sawTerminal) break
      }

      if (sawTerminal) return
      if (needResync) {
        cursor = 0
        continue
      }
      // 服务端把流关了但没给终态：按 I13 由上层对账，这里重连补齐。
      throw new Error('事件流提前结束')
    } catch (error) {
      clearTimeout(connectTimeout)
      if (signal.aborted) return
      attempt += 1
      if (attempt > maxAttempts) {
        yield { kind: 'degraded', reason: (error as Error).message }
        return
      }
      const delayMs = backoffDelay(attempt, random)
      yield { kind: 'reconnect', attempt, delayMs }
      try {
        await sleepImpl(delayMs, signal)
      } catch {
        return // 被取消：静默退出（abort 不是错误）
      }
    } finally {
      clearTimeout(connectTimeout)
      signal.removeEventListener('abort', onAbort)
    }
  }
}
