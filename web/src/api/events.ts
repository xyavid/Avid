/**
 * SSE consumer for one run's event stream (`GET /api/runs/{id}/events`).
 * Wire format: `id: <seq>` (durable only) + `event: <type>` + `data: <json>`, frames split by a
 * blank line and `: ping` as heartbeat; business fields sit under `data`. Deltas never replay:
 * subscribing with an `after` cursor resumes durable events, and `run_finished` covers live text.
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

/** Incremental parser: joins frames across chunks, skips comment frames, merges multi-line data. */
export function createSseParser({ onEvent }: { onEvent: (event: RunEventMessage) => void }) {
  let buffer = ''

  const dispatch = (block: string) => {
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith(':')) continue // heartbeat / comment
      if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart())
      }
      // id:/event: lines are not read: the event payload carries seq and type, data is truth.
    }
    if (dataLines.length === 0) return
    let parsed: unknown
    try {
      parsed = JSON.parse(dataLines.join('\n'))
    } catch {
      return // drop a malformed frame; one bad data line must not break the stream
    }
    const frame = parsed as RunEventMessage
    // Normalize: a missing seq becomes null (delta frames have no id line)
    onEvent({ ...frame, seq: frame.seq ?? null })
  }

  return {
    /** Feed an arbitrary chunk; one chunk may split a frame in half. */
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
  /** Current durable cursor: pass it back as `after` when reconnecting. */
  cursor: () => number
  abort: () => void
}

/**
 * Subscribe to a run's event stream; onError fires only on an abnormal disconnect (a normal
 * server-side end is not an error). The caller decides whether to reconnect or poll instead.
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
      // Normal stream close: the run is terminal (the server closes the stream once), not an error.
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

/** Typed payload accessor: `approval_requested` data. */
export type ApprovalRequestedData = {
  approval_id: string
  tool: string
  arguments: string
  reason: string
}

/** `run_finished` data: terminal text (if any) and the final usage snapshot. */
export type RunFinishedData = { text?: string; usage?: UsageReport }
