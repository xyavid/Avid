/**
 * Lifecycle of one run: send → subscribe → merge items → settle. Phases: idle → starting (POST run)
 * → running (SSE subscribed) → settling (terminal event received; the page refetches durable and
 * calls settle) → idle; `error` = send or subscribe failure. Holds only this run's items and the
 * page merges them into history via `timeline.mergeItems`; a broken stream resumes from its cursor
 * a bounded number of times before falling back to polling getRun.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  answerApproval,
  cancelRun,
  decideApproval,
  dropInput,
  getRun,
  listInputs,
  startRun,
  submitInput,
} from '../api/client'
import { subscribeRun } from '../api/events'
import type { RunEventMessage } from '../api/events'
import type { UsageReport } from '../api/types'
import type { RunPermission } from '../events/types'
import type { TimelineEvent, TimelineItem } from './timeline'
import { appendPending, appendUser, applyEvent, dropPending, subagentTag } from './timeline'
import type { DraftImage } from './imagePrep'

export type RunPhase = 'idle' | 'starting' | 'running' | 'settling' | 'error'

/** Frame-buffer key of the parent run; a sub-run's key is `{task}\u0000{index}`. */
const PARENT_KEY = ''

/** Reattach attempts before falling back to polling: the cursor makes each one cheap, not free. */
const MAX_REATTACH = 3
const REATTACH_BASE_MS = 500

/** One pending streaming delta: text, first-piece arrival time, origin (null = parent run). */
type PendingDelta = { text: string; ts: number; tag: { task: string; index: number } | null }

/** One pending decision: kind='approval' wants a decision, 'question' an answer (one UI slot). */
export type LiveApproval = {
  approvalId: string
  kind: 'approval' | 'question'
  tool: string
  arguments: string
  reason: string
  /** Choice options (empty = free-form answer). */
  options: string[]
}

/** Event arguments may be a string or an object: render both readably, never `[object Object]`. */
function argumentsText(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

/** Event frame: only these four fields are read; `ts` measures a reasoning block's duration. */
type EventFrame = { type: string; ts: number; data?: Record<string, unknown> }

export type RunStream = ReturnType<typeof useRunStream>

/**
 * @param activeRunId the selected session's run as the session list reports it, or null: the hook
 * follows it, so switching to a session with a run in flight picks that run up and leaving one
 * stops reading it (the run itself keeps going on the server).
 */
export function useRunStream(
  sessionId: string | null,
  onSettled: () => void,
  activeRunId: string | null = null,
) {
  const [phase, setPhase] = useState<RunPhase>('idle')
  const [runId, setRunId] = useState<string | null>(null)
  const [items, setItems] = useState<TimelineItem[]>([])
  // Which session items belong to: they do not follow a session switch, and switching
  // back merges by entry_id.
  const [attachedSession, setAttachedSession] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<LiveApproval[]>([])
  // Per-round usage snapshot (run_status, then run_finished): keeps the context ring live.
  const [usage, setUsage] = useState<UsageReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Actual permission form reported by run_started (normal/full); null = not known yet.
  const [runPermission, setRunPermission] = useState<RunPermission | null>(null)

  const runIdRef = useRef<string | null>(null)
  const sessionRef = useRef<string | null>(null)
  sessionRef.current = attachedSession
  // The follow effect reacts to the selection, not to the state it changes, so it reads this ref.
  const attachedRef = useRef<string | null>(null)
  attachedRef.current = attachedSession
  const subRef = useRef<{ abort: () => void } | null>(null)
  const settlingRef = useRef(false)
  // Where the stream last got to (durable frames only) and how many reattaches this run has spent.
  const cursorRef = useRef(0)
  const reattachRef = useRef(0)
  const reattachTimerRef = useRef<number | null>(null)
  /** Drop a pending reattach: nothing about this run should bring the stream back. */
  const clearReattach = useCallback(() => {
    if (reattachTimerRef.current !== null) {
      window.clearTimeout(reattachTimerRef.current)
      reattachTimerRef.current = null
    }
  }, [])
  // Deltas buffer in a ref and flush at most once per animation frame, collapsing markdown
  // re-parsing with them. Background tabs get a 1s fallback timer (no frames there), so delta text
  // lands within a second: never lost, never unbounded. Buffering is per origin — the parent and
  // each sub-run form separate streams, so one run's text never mixes into another's timeline.
  // The pending flag is a boolean, not the frame handle: a synchronous rAF stub would overwrite a
  // consumed handle and stall every later delta.
  const pendingFrameRef = useRef(false)
  const frameHandleRef = useRef<number | null>(null)
  const fallbackTimerRef = useRef<number | null>(null)
  const pendingRef = useRef(new Map<string, PendingDelta>())
  const flushDeltas = useCallback(() => {
    pendingFrameRef.current = false
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
    if (pendingRef.current.size === 0) return
    const chunks = [...pendingRef.current.values()]
    pendingRef.current.clear()
    setItems((cur) =>
      chunks.reduce((acc, chunk) => {
        const event: TimelineEvent = {
          type: 'assistant_delta',
          ts: chunk.ts,
          data: chunk.tag === null ? { text: chunk.text } : { text: chunk.text, subagent: chunk.tag },
        }
        return applyEvent(acc, event)
      }, cur),
    )
  }, [])
  const scheduleDeltaFlush = useCallback(() => {
    if (pendingFrameRef.current) return
    pendingFrameRef.current = true
    const flush = () => {
      pendingFrameRef.current = false
      if (frameHandleRef.current !== null) {
        cancelAnimationFrame(frameHandleRef.current)
        frameHandleRef.current = null
      }
      if (fallbackTimerRef.current !== null) {
        window.clearTimeout(fallbackTimerRef.current)
        fallbackTimerRef.current = null
      }
      flushDeltas()
    }
    frameHandleRef.current = requestAnimationFrame(flush)
    fallbackTimerRef.current = window.setTimeout(flush, 1000)
  }, [flushDeltas])
  /** Invalidate every origin's unflushed deltas (terminal state / reset). */
  const discardPendingDeltas = useCallback(() => {
    pendingFrameRef.current = false
    if (frameHandleRef.current !== null) {
      cancelAnimationFrame(frameHandleRef.current)
      frameHandleRef.current = null
    }
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current)
      fallbackTimerRef.current = null
    }
    pendingRef.current.clear()
  }, [])
  /** Invalidate only the parent run's unflushed deltas: its persisted message supersedes them,
   *  while a sub-run's deltas are still in flight and must not be dropped. */
  const discardParentDeltas = useCallback(() => {
    pendingRef.current.delete(PARENT_KEY)
  }, [])
  useEffect(
    () => () => {
      discardPendingDeltas()
      clearReattach()
    },
    [clearReattach, discardPendingDeltas],
  )

  /**
   * Settle: drop subscription and run state but keep `items` (the timeline must not jump).
   * `error` is not cleared either — a run failure is that run's only trace — only the next `send`
   * or `attach` clears it.
   */
  const settle = useCallback(() => {
    subRef.current?.abort()
    subRef.current = null
    clearReattach()
    runIdRef.current = null
    settlingRef.current = false
    discardPendingDeltas()
    setRunId(null)
    setApprovals([])
    setPhase('idle')
  }, [clearReattach, discardPendingDeltas])

  /** A run being followed for the first time starts its own cursor and reattach budget. */
  const beginStream = useCallback(() => {
    clearReattach()
    cursorRef.current = 0
    reattachRef.current = 0
  }, [clearReattach])

  /** Usage snapshot carried by an event (shape guaranteed by the kernel); a subagent-tagged one
   *  belongs to a sub-run and must not replace the parent's reading. */
  const takeUsage = (data: Record<string, unknown>) => {
    if (data.subagent !== undefined) return
    const report = data.usage
    if (report !== null && typeof report === 'object') setUsage(report as UsageReport)
  }

  const handleEvent = useCallback(
    (e: EventFrame) => {
      const data = (e.data ?? {}) as Record<string, unknown>
      switch (e.type) {
        case 'run_started': {
          // normal = default (destructive commands ask once); full = full access.
          // Sandbox facts live in sandbox_state / sandbox_notes and are not shown yet.
          if (data.permission === 'normal' || data.permission === 'full') {
            setRunPermission(data.permission)
          }
          return
        }
        case 'assistant_delta': {
          const text = data.text
          if (typeof text === 'string' && text) {
            const tag = subagentTag(data)
            const key = tag === null ? PARENT_KEY : `${tag.task}\u0000${tag.index}`
            const pending = pendingRef.current.get(key)
            pendingRef.current.set(key, { text: (pending?.text ?? '') + text, ts: pending?.ts ?? e.ts, tag })
            scheduleDeltaFlush()
          }
          return
        }
        case 'assistant_message': {
          // The parent's persisted message supersedes accumulated deltas (on replay deltas are
          // lost, durable is authoritative); a sub-run's deltas are unaffected.
          discardParentDeltas()
          setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
          return
        }
        case 'approval_requested': {
          setApprovals((a) => [
            ...a,
            {
              approvalId: String(data.approval_id ?? ''),
              kind: data.kind === 'question' ? 'question' : 'approval',
              tool: String(data.tool ?? ''),
              arguments: argumentsText(data.arguments),
              reason: String(data.reason ?? ''),
              options: Array.isArray(data.options) ? data.options.map((item) => String(item)) : [],
            },
          ])
          return
        }
        case 'approval_resolved': {
          const id = String(data.approval_id ?? '')
          setApprovals((a) => a.filter((x) => x.approvalId !== id))
          return
        }
        case 'run_finished':
        case 'run_failed':
        case 'run_cancelled': {
          if (settlingRef.current) return
          settlingRef.current = true
          discardPendingDeltas()
          takeUsage(data)
          if (e.type === 'run_failed') {
            setError(String(data.message ?? '运行失败'))
            // The failure item enters the timeline (with entry_id, aligned with the reloaded
            // entry); the outer error line covers the window before that entry is written.
            setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
          }
          setPhase('settling')
          onSettled()
          void drainNextTurn()
          return
        }
        default: {
          // Everything else goes to the merge: unrelated types are no-ops returning the same array,
          // so React does not re-render; usage-carrying events (run_status) are taken in passing.
          takeUsage(data)
          setItems((cur) => applyEvent(cur, { type: e.type, ts: e.ts, data }))
        }
      }
    },
    [discardParentDeltas, discardPendingDeltas, onSettled, scheduleDeltaFlush],
  )

  const pollUntilTerminal = useCallback(
    async (id: string) => {
      for (;;) {
        try {
          const run = await getRun(id)
          if (['finished', 'failed', 'cancelled'].includes(run.status)) break
        } catch {
          break // cannot even reach getRun: stop polling, a user refresh is the fallback
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
      if (!settlingRef.current) {
        settlingRef.current = true
        setPhase('settling')
        onSettled()
      }
    },
    [onSettled],
  )

  const subscribe = useCallback(
    (id: string, after = 0) => {
      // One run is followed at a time, and the stream is closed by an abort that lands a moment
      // later: frames already parsed still arrive, and a slow open can resolve after the switch.
      // Both are checked against the run being followed, so an abandoned run cannot move this
      // page's state or leave a second subscription behind.
      const onEvent = (event: RunEventMessage) => {
        // Only durable frames carry a seq, and only those replay: the cursor is where to resume.
        if (event.seq !== null) cursorRef.current = event.seq
        if (runIdRef.current === id) handleEvent(event)
      }
      const onError = (err: Error) => {
        if (runIdRef.current !== id || settlingRef.current) return
        // A dropped stream is an interruption, not an ending: the run continues server-side and
        // the cursor makes resuming cheap. Bounded, because a backend that is really gone should
        // not be hammered by a page left open — the poll below is the honest fallback.
        if (reattachRef.current < MAX_REATTACH) {
          const delay = REATTACH_BASE_MS * 2 ** reattachRef.current
          reattachRef.current += 1
          reattachTimerRef.current = window.setTimeout(() => {
            reattachTimerRef.current = null
            if (runIdRef.current === id && !settlingRef.current) subscribe(id, cursorRef.current)
          }, delay)
          return
        }
        setError(err.message)
        void pollUntilTerminal(id)
      }
      void subscribeRun(id, after, { onEvent, onError }).then((sub) => {
        if (runIdRef.current !== id) {
          sub.abort()
          return
        }
        subRef.current = sub
      })
    },
    [handleEvent, pollUntilTerminal],
  )

  const send = useCallback(
    async (
      prompt: string,
      full: boolean,
      model?: string | null,
      branch?: string,
      effort?: string | null,
      images: DraftImage[] = [],
    ) => {
      if (!sessionId) return
      // A previous run's stream must not stay open beside this one: the server ends it after the
      // terminal event, and that can land after this send (a fast second question).
      subRef.current?.abort()
      subRef.current = null
      beginStream()
      setPhase('starting')
      setError(null)
      discardPendingDeltas()
      // A different session starts a fresh list: the previous session's items do not follow.
      const optimistic = images.map((image) => ({
        source: 'local' as const,
        url: image.url,
        name: image.name,
      }))
      setItems((cur) =>
        sessionRef.current === sessionId
          ? appendUser(cur, prompt, optimistic)
          : appendUser([], prompt, optimistic),
      )
      setAttachedSession(sessionId)
      setApprovals([])
      setRunPermission(null)
      try {
        const created = await startRun(sessionId, {
          prompt,
          // Omit when there are no images: a text-only request must not carry an empty images field
          ...(images.length
            ? { images: images.map((image) => ({ name: image.name, data: image.data })) }
            : {}),
          // Send model only when overridden; omitting it lets the server resolve from settings
          ...(model ? { model } : {}),
          // Same for the level: omitting it means the parameter is not sent at all
          ...(effort ? { reasoning_effort: effort } : {}),
          // Send branch only when it is not main; main is the server default
          ...(branch && branch !== 'main' ? { branch } : {}),
          // The only full-access credential; the default form does not send this field.
          ...(full ? { full_access_ack: true } : {}),
        })
        runIdRef.current = created.run_id
        setRunId(created.run_id)
        setPhase('running')
        subscribe(created.run_id)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        setPhase('error')
      }
    },
    [discardPendingDeltas, sessionId, subscribe],
  )

  const attach = useCallback(
    (id: string) => {
      settle()
      beginStream()
      setError(null) // a new stream: the previous one's failure no longer applies
      setItems([])
      setAttachedSession(sessionId)
      runIdRef.current = id
      setRunId(id)
      setPhase('running')
      subscribe(id)
    },
    [beginStream, sessionId, settle, subscribe],
  )

  // The page follows one run at a time: the selected session's. Leaving a session stops reading
  // its run (the run keeps going on the server, and the session list keeps its running mark) and
  // keeps its items for a switch back; the newly selected session's live run is picked up where
  // it stands. Both checks read refs, so the effect fires on the selection, not on the state it
  // changes.
  useEffect(() => {
    if (sessionId === null) return
    if (attachedRef.current !== null && attachedRef.current !== sessionId) settle()
    if (activeRunId !== null && runIdRef.current !== activeRunId) attach(activeRunId)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reacts to the selection only
  }, [sessionId, activeRunId])

  /** After a terminal state, start the next queued input's run; the server never starts one on its own. */
  const drainNextTurn = useCallback(async () => {
    if (!sessionId) return
    try {
      const items = await listInputs(sessionId)
      const next = items.find((item) => item.mode === 'after')
      if (next === undefined) return
      const created = await startRun(sessionId, { from_input: next.input_id })
      attach(created.run_id)
    } catch {
      // Lost the claim (another tab) or the server is down: retry on the next terminal state.
    }
  }, [sessionId, attach])

  /** Submit a supplemental input: `after` waits for the next turn, `now` lands in the current
   *  run's next step. Accepted is not in-context: queued items render as pending until persisted. */
  const submit = useCallback(
    async (text: string, mode: 'now' | 'after', images: DraftImage[] = []) => {
      if (!sessionId) return
      setError(null)
      const clientId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
      try {
        const accepted = await submitInput(sessionId, {
          mode,
          prompt: text,
          client_id: clientId,
          ...(images.length
            ? { images: images.map((image) => ({ name: image.name, data: image.data })) }
            : {}),
        })
        if (accepted.kind === 'run' && accepted.run_id !== null) {
          // Idle: the server started a run; attach to its stream (events replay history + this message)
          attach(accepted.run_id)
          return
        }
        const inputId = accepted.input_id ?? clientId
        setItems((cur) =>
          appendPending(cur, {
            inputId,
            mode: accepted.mode,
            missed: false,
            images: images.length,
            text,
          }),
        )
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [attach, sessionId],
  )

  /** Drop a pending input that hasn't been claimed yet. */
  const dropInputById = useCallback(
    async (inputId: string) => {
      if (!sessionId) return
      try {
        await dropInput(sessionId, inputId)
        setItems((cur) => dropPending(cur, inputId))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    [sessionId],
  )

  const stop = useCallback(async () => {
    const id = runIdRef.current
    if (id) await cancelRun(id).catch(() => {})
  }, [])

  const decide = useCallback(
    async (approvalId: string, decision: 'allow' | 'deny') => {
      const id = runIdRef.current
      if (!id) return
      await decideApproval(id, approvalId, decision).catch(() => {})
    },
    [],
  )

  /** Answer a question: same endpoint as a decision, payload swaps to `answer`. */
  const answer = useCallback(async (approvalId: string, text: string) => {
    const id = runIdRef.current
    if (!id) return
    await answerApproval(id, approvalId, text).catch(() => {})
  }, [])

  return {
    phase,
    runId,
    items,
    attachedSession,
    approvals,
    runPermission,
    usage,
    error,
    send,
    submit,
    dropInput: dropInputById,
    stop,
    decide,
    answer,
    attach,
    settle,
  }
}
