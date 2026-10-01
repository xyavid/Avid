/**
 * SSE 消费层：一次运行的事件流（`GET /api/runs/{id}/events`）。
 *
 * 线格式（src/avid/web/sse.py）：`id: <seq>`（仅 durable 事件）+ `event: <type>` +
 * `data: <json>`，帧间空行分隔；心跳是 `: ping` 注释帧。业务字段在 data 的
 * `data` 键里（event_payload：外层是 run_id/session_id/seq/ts/type）。
 *
 * 重放与续接：订阅时带 after 游标，服务端从该 seq 重放 durable；delta 永不重放，
 * 所以断线重连后只见 durable 事实，飞着的增量文本靠 run_finished 兜底。
 */

import type { UsageReport } from './types'

export type RunEventFrame = {
  run_id: string
  session_id: string
  seq: number | null
  ts: number
  type: string
  data: Record<string, unknown>
}

export type RunEventMessage = RunEventFrame & { data: Record<string, unknown> }

export type SseHandlers = {
  onEvent: (event: RunEventMessage) => void
  onError: (error: Error) => void
}

/** 增量喂入的解析器：跨 chunk 拼帧、跳过注释、多行 data 按规范合并。 */
export function createSseParser({ onEvent }: { onEvent: (event: RunEventMessage) => void }) {
  let buffer = ''

  const dispatch = (block: string) => {
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue // 心跳/注释
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
      // id:/event: 行不单独取——event_payload 自带 seq 与 type，data 即真相。
    }
    if (dataLines.length === 0) return
    let parsed: unknown
    try {
      parsed = JSON.parse(dataLines.join('\n'))
    } catch {
      return // 非法帧丢弃，不让一条坏数据中断整条流
    }
    const frame = parsed as RunEventMessage
    // 归一：缺失的 seq 一律给 null（delta 帧没有 id 行）
    onEvent({ ...frame, seq: frame.seq ?? null })
  }

  return {
    /** 喂入任意长度的文本块；块可能把一帧劈成两半。 */
    feed(chunk: string) {
      buffer += chunk
      let index: number
      while ((index = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, index)
        buffer = buffer.slice(index + 2)
        if (block.trim()) dispatch(block)
      }
    },
  }
}

export type LiveSubscription = {
  /** 当前 durable 游标：断线重连时作为 after 传回。 */
  cursor: () => number
  abort: () => void
}

/**
 * 订阅一次运行的事件流；onError 只在流异常断开时触发（服务端正常收尾不算）。
 * 返回游标读取器与中止柄——调用方决定是否带游标重连或改走轮询兜底。
 */
export async function subscribeRun(
  runId: string,
  after: number,
  handlers: SseHandlers,
  externalSignal?: AbortSignal,
): Promise<LiveSubscription> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  externalSignal?.addEventListener('abort', abort)

  let cursor = after

  const onEvent = (event: RunEventMessage) => {
    if (event.seq !== null && event.seq !== undefined) {
      cursor = event.seq
    }
    handlers.onEvent(event)
  }

  void (async () => {
    try {
      const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/events?after=${cursor}&deltas=1`, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
      })
      if (!res.ok || res.body === null) {
        handlers.onError(new Error(`事件流打开失败（HTTP ${res.status}）`))
        return
      }
      const parser = createSseParser({ onEvent })
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        parser.feed(decoder.decode(value, { stream: true }))
      }
      // 流正常关闭：运行已终态（服务端流一次即收），不当作错误。
    } catch (err) {
      if (!controller.signal.aborted) {
        handlers.onError(err instanceof Error ? err : new Error(String(err)))
      }
    }
  })()

  return {
    cursor: () => cursor,
    abort,
  }
}

/** 事件载荷的类型化取值助手：approval_requested 的 data。 */
export type ApprovalRequestedData = {
  approval_id: string
  tool: string
  arguments: string
  reason: string
}

/** run_finished 的 data：终态文本（若有）与最终用量快照。 */
export type RunFinishedData = { text?: string; usage?: UsageReport }
