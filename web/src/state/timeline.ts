/**
 * Timeline: ordered items of a session (history plus each run), shared by live and settled views.
 * Invariant: for one run, items built from the event stream are item-by-item identical to those
 * rebuilt from the session entries (live-only items aside); merging is idempotent by `entry_id` /
 * `tool_call_id`, so a mid-run refresh that replays from seq 0 adds no duplicate items.
 */

import type { Entry } from '../api/types'
import { subagentTasks } from './toolArgs'

/** Tool-row status. Kernel `truncated` maps to ok and `denied` to failed; a session reload carries
 *  neither flag, so both are recognized from the result text (`classifyToolResult`). */
export type ToolStatus = 'running' | 'ok' | 'failed'

/**
 * One subagent task's own item list, on the same model as the parent timeline.
 * `task` / `index` come from the persisted arguments; `items` come from the event stream
 * (not persisted, not replayed) and are empty after a reload.
 */
export type SubagentRun = {
  task: string
  index: number
  items: TimelineItem[]
}

/** One step inside a subagent (flat shape for the card's collapsed rows), derived from `runs`. */
export type SubagentStep = {
  task: string
  callId: string
  name: string
  args: string
  status: ToolStatus
}

/**
 * Message reading in ms, used for a turn's duration (`turnGroups` takes answer minus user).
 * The live path reads the event `ts`, the reload path the entry `timestamp`; both are the same
 * write, so the difference is disk latency and the settled row still reads the same. Null on both
 * paths = no reading (the collapsed row only says "done").
 */
export type MessageTs = number | null

/**
 * An image in a user message: a local draft (object URL, not yet persisted) or a stored
 * entry ref (entryId, block index). URL shape lives in `api/`; the view composes it.
 */
export type TimelineImage =
  | { source: 'local'; url: string; name: string | null }
  | {
      source: 'stored'
      entryId: string
      index: number
      name: string | null
      bytes: number | null
      mime: string
    }

/**
 * A not-yet-adopted input attached to a user item: accepted, not yet in the model context.
 * The mark clears when the entry lands (the event carries `input_id`).
 */
export type TimelinePending = {
  inputId: string
  mode: string
  /** Downgraded from 'now' to 'after' because its run ended before claiming it. */
  missed: boolean
  /** Image count: bytes are only reachable after the entry lands. */
  images: number
}

export type TimelineItem =
  | {
      kind: 'user'
      entryId: string | null
      text: string
      images?: TimelineImage[]
      pending?: TimelinePending
      ts: MessageTs
    }
  /** Run-failure record: the kernel persists it as an error entry — visible to people and on
   *  reload, invisible to the model. */
  | { kind: 'error'; entryId: string | null; text: string }
  | { kind: 'assistant'; entryId: string | null; text: string; streaming: boolean; ts: MessageTs }
  | { kind: 'reasoning'; text: string; startedAt: number; endedAt: number; streaming: boolean }
  | {
      kind: 'tool'
      callId: string
      name: string
      args: string
      result: string | null
      status: ToolStatus
      durationMs: number | null
      /** Each sub-run's items; always an empty array for non-subagent tools. */
      runs: SubagentRun[]
    }

/** Minimal kernel event; `ts` measures a reasoning block's duration. */
export type TimelineEvent = { type: string; ts: number; data?: Record<string, unknown> }

type MessagePayload = {
  role?: unknown
  content?: unknown
  tool_calls?: Array<{ id?: unknown; function?: { name?: unknown; arguments?: unknown } }>
  tool_call_id?: unknown
}

/** Same rule as `classify_tool_status` in `avid/web/schemas.py` (prefixes, plus a marker within
 *  the first 64 chars) and the kernel's fixed denial text; the two sides must change together. */
const FAILED_PREFIXES = ['错误：', '参数错误：', 'Permission denied.']
const FAILED_MARK = '执行失败：'

export function classifyToolResult(content: string): ToolStatus {
  const failed =
    FAILED_PREFIXES.some((prefix) => content.startsWith(prefix)) ||
    content.slice(0, 64).includes(FAILED_MARK)
  return failed ? 'failed' : 'ok'
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function messageContent(value: unknown): string {
  if (typeof value === 'string') return value
  return value === undefined || value === null ? '' : JSON.stringify(value)
}

/** User message content → (text, images): text parts join the body, image parts become refs.
 *  The single parse point, so the event stream and the session reload produce identical items. */
export function userContent(
  content: unknown,
  entryId: string | null,
): { text: string; images: TimelineImage[] } {
  if (!Array.isArray(content)) return { text: messageContent(content), images: [] }
  const texts: string[] = []
  const images: TimelineImage[] = []
  content.forEach((part, index) => {
    if (part === null || typeof part !== 'object') return
    const block = part as Record<string, unknown>
    if (block.type === 'image') {
      if (entryId === null) return // no entry, no readable URL: waits for the user_message event
      images.push({
        source: 'stored',
        entryId,
        index,
        name: typeof block.name === 'string' ? block.name : null,
        bytes: typeof block.bytes === 'number' ? block.bytes : null,
        mime: typeof block.mime === 'string' ? block.mime : '',
      })
      return
    }
    if (typeof block.text === 'string' && block.text) texts.push(block.text)
  })
  return { text: texts.join('\n'), images }
}

/** Dedup identity: an item with one (persisted message, tool call) is the same on both paths. */
function identity(item: TimelineItem): string | null {
  if (item.kind === 'tool') return `tool:${item.callId}`
  if (item.kind === 'user' || item.kind === 'assistant' || item.kind === 'error') {
    return item.entryId === null ? null : `entry:${item.entryId}`
  }
  return null
}

/** React key: identity when available, else the index (the list only appends, so it is stable). */
export function itemKey(item: TimelineItem, index: number): string {
  return identity(item) ?? `live:${item.kind}:${index}`
}

/** Optimistic user item on send, adopted in place when `user_message` arrives. Draft images keep
 *  their local object URL until `applyUserMessage` folds in the entry ref. */
export function appendUser(
  items: TimelineItem[],
  text: string,
  images: TimelineImage[] = [],
): TimelineItem[] {
  return [
    ...items,
    { kind: 'user', entryId: null, text, ...(images.length ? { images } : {}), ts: Date.now() },
  ]
}

/** Accept a pending input (submit succeeded, not yet persisted): one item per inputId. */
export function appendPending(
  items: TimelineItem[],
  input: { inputId: string; mode: string; missed: boolean; images: number; text: string },
): TimelineItem[] {
  if (items.some((item) => item.kind === 'user' && item.pending?.inputId === input.inputId)) {
    return items
  }
  return [
    ...items,
    {
      kind: 'user',
      entryId: null,
      text: input.text,
      pending: {
        inputId: input.inputId,
        mode: input.mode,
        missed: input.missed,
        images: input.images,
      },
      ts: Date.now(),
    },
  ]
}

/** Remove a pending input item; called once the server confirms the drop. */
export function dropPending(items: TimelineItem[], inputId: string): TimelineItem[] {
  const has = items.some((item) => item.kind === 'user' && item.pending?.inputId === inputId)
  if (!has) return items
  return items.filter((item) => !(item.kind === 'user' && item.pending?.inputId === inputId))
}

/** Draw the server's pending inputs back in when a session opens, so they survive a reload. */
export function mergePendingInputs(
  items: TimelineItem[],
  inputs: {
    input_id: string
    mode: string
    text: string
    images: number
    missed: boolean
  }[],
): TimelineItem[] {
  let merged = items
  for (const input of inputs) {
    merged = appendPending(merged, {
      inputId: input.input_id,
      mode: input.mode,
      missed: input.missed,
      images: input.images,
      text: input.text,
    })
  }
  return merged
}

/** Bottom-follow signature: item count + content volume; a change means something new landed. */
export function timelineSignature(items: TimelineItem[]): string {
  let chars = 0
  for (const item of items) {
    if (item.kind === 'tool') {
      const children = item.runs.reduce((n, run) => n + run.items.length, 0)
      chars += item.args.length + (item.result?.length ?? 0) + children * 32
    } else chars += item.text.length
  }
  return `${items.length}:${chars}`
}

function indexOfTool(items: TimelineItem[], callId: string): number {
  return items.findIndex((item) => item.kind === 'tool' && item.callId === callId)
}

function lastIndexOf(items: TimelineItem[], match: (item: TimelineItem) => boolean): number {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (match(items[index]!)) return index
  }
  return -1
}

function replace<T>(items: T[], index: number, item: T): T[] {
  return [...items.slice(0, index), item, ...items.slice(index + 1)]
}

function emptyTool(callId: string, name: string, args: string): TimelineItem {
  return {
    kind: 'tool',
    callId,
    name,
    args,
    result: null,
    status: 'running',
    durationMs: null,
    runs: name === 'subagent' ? seedRuns(args) : [],
  }
}

/** Seed sub-runs from the argument task list, so a reload still lists them (details empty). */
function seedRuns(args: string): SubagentRun[] {
  return subagentTasks(args).map((task, index) => ({ task, index, items: [] }))
}

/** Close a streaming reasoning item once text or tools arrive; only reasoning — the streaming
 *  assistant item is committed in place by `assistant_message`, closing it early would lose it. */
function closeReasoning(items: TimelineItem[]): TimelineItem[] {
  if (!items.some((item) => item.kind === 'reasoning' && item.streaming)) return items
  return items.map((item) =>
    item.kind === 'reasoning' && item.streaming ? { ...item, streaming: false } : item,
  )
}

// ---------------------------------------------------------------- session entries → items

/** Session entries → items. Turn order: the model speaks first, then the tools it called. */
export function itemsFromEntries(entries: Entry[]): TimelineItem[] {
  const items: TimelineItem[] = []
  const byCallId = new Map<string, number>()
  for (const entry of entries) {
    if (entry.type === 'error') {
      const text = messageContent((entry.message as MessagePayload | null)?.content)
      if (text) items.push({ kind: 'error', entryId: entry.entry_id, text })
      continue
    }
    if (entry.type !== 'message' || entry.message === null) continue
    const message = entry.message as MessagePayload
    const role = str(message.role)
    if (role === 'user') {
      const { text, images } = userContent(message.content, entry.entry_id)
      if (text || images.length > 0) {
        items.push({
          kind: 'user',
          entryId: entry.entry_id,
          text,
          ...(images.length ? { images } : {}),
          ts: entry.timestamp,
        })
      }
      continue
    }
    if (role === 'assistant') {
      const text = messageContent(message.content)
      if (text.trim()) {
        items.push({ kind: 'assistant', entryId: entry.entry_id, text, streaming: false, ts: entry.timestamp })
      }
      for (const call of message.tool_calls ?? []) {
        const callId = str(call.id)
        const name = str(call.function?.name)
        if (!callId || !name) continue
        byCallId.set(callId, items.length)
        items.push(emptyTool(callId, name, str(call.function?.arguments)))
      }
      continue
    }
    if (role === 'tool') {
      const callId = str(message.tool_call_id)
      const at = byCallId.get(callId)
      if (at === undefined) continue
      const content = messageContent(message.content)
      const item = items[at]
      if (item?.kind !== 'tool') continue
      items[at] = { ...item, result: content, status: classifyToolResult(content) }
    }
  }
  return items
}

// ---------------------------------------------------------------- events → item deltas

export function subagentTag(data: Record<string, unknown>): { task: string; index: number } | null {
  const raw = data.subagent
  if (raw === null || typeof raw !== 'object') return null
  const tag = raw as Record<string, unknown>
  if (typeof tag.task !== 'string' || typeof tag.index !== 'number') return null
  return { task: tag.task, index: tag.index }
}

function isSubagentCard(item: TimelineItem): item is Extract<TimelineItem, { kind: 'tool' }> {
  return item.kind === 'tool' && item.name === 'subagent'
}

/**
 * Which card a tagged event belongs to, in a deliberate order: a running card that already claimed
 * this task (a re-dispatched task must not steal the old card's events), then the latest running
 * subagent card, then the latest subagent card — a finished batch's deltas may still sit in the
 * frame buffer, and its events must still find a home.
 */
function subagentCardIndex(items: TimelineItem[], tag: { task: string; index: number }): number {
  const owns = (item: TimelineItem) =>
    isSubagentCard(item) && item.runs.some((run) => run.task === tag.task || run.index === tag.index)
  const byIdentity = lastIndexOf(items, (item) => owns(item) && item.kind === 'tool' && item.status === 'running')
  if (byIdentity >= 0) return byIdentity
  const running = lastIndexOf(items, (item) => isSubagentCard(item) && item.status === 'running')
  if (running >= 0) return running
  return lastIndexOf(items, isSubagentCard)
}

/** Claim a sub-run by task name, appending one by index when the arguments lack that task. */
function runIndex(runs: SubagentRun[], tag: { task: string; index: number }): number {
  const at = runs.findIndex((run) => run.task === tag.task)
  if (at >= 0) return at
  const byIndex = runs.findIndex((run) => run.index === tag.index)
  return byIndex
}

/** Batch finished: close the sub-run's streaming text so the cursor stops blinking in the panel. */
function closeRunStreams(item: TimelineItem): TimelineItem {
  if (item.kind !== 'tool' || item.status === 'running' || item.runs.length === 0) return item
  const runs = item.runs.map((run) => ({
    ...run,
    items: run.items.map((child) =>
      child.kind === 'assistant' && child.streaming ? { ...child, streaming: false } : child,
    ),
  }))
  return { ...item, runs }
}

/** A sub-run's own item deltas: the same merge functions, applied to its own list. */
function childItems(items: TimelineItem[], event: TimelineEvent, data: Record<string, unknown>): TimelineItem[] {
  switch (event.type) {
    case 'assistant_delta':
      return applyAssistantDelta(items, data)
    case 'reasoning_delta':
      return applyReasoningDelta(items, event, data)
    case 'tool_call_started':
    case 'tool_call_finished':
    case 'tool_call_denied':
      return applyToolEvent(items, event)
    default:
      // Other sub-run events (run_status / run_started / stop_nudge…) stay out of its body:
      // status and readings live on the parent card; the body keeps only words and actions.
      return items
  }
}

/** Fold a tagged event into its sub-run; null when attribution fails, and the caller falls back
 *  to a parent-level row. */
function withChildEvent(
  items: TimelineItem[],
  event: TimelineEvent,
  tag: { task: string; index: number },
): TimelineItem[] | null {
  const at = subagentCardIndex(items, tag)
  if (at < 0) return null
  const card = items[at]
  if (card?.kind !== 'tool') return null

  const found = runIndex(card.runs, tag)
  const current: SubagentRun =
    found >= 0 ? card.runs[found]! : { task: tag.task, index: tag.index, items: [] }
  const nextItems = childItems(current.items, event, event.data ?? {})
  if (nextItems === current.items && found >= 0) return items // unrelated event: leave the list

  const runs =
    found >= 0
      ? replace(card.runs, found, { ...current, items: nextItems })
      : [...card.runs, { ...current, items: nextItems }]
  // Card already settled (a late delta): close the stream, no further event will
  return replace(items, at, closeRunStreams({ ...card, runs }))
}

/** One event into the list; unknown events and live-only status events return it unchanged.
 *  Pure and idempotent: replaying the same event does not change the list. */
export function applyEvent(items: TimelineItem[], event: TimelineEvent): TimelineItem[] {
  const data = event.data ?? {}
  const tag = subagentTag(data)
  if (tag) {
    // Everything of a sub-run folds into its own item list (text, reasoning, tools); when
    // attribution fails, fall back to one parent row — an extra row beats lost information.
    const folded = withChildEvent(items, event, tag)
    if (folded !== null) return folded
    if (event.type.startsWith('tool_call_')) return applyToolEvent(items, event)
    return items
  }
  switch (event.type) {
    case 'run_failed':
      return applyRunFailed(items, data)
    case 'user_message':
      return applyUserMessage(items, event, data)
    case 'assistant_message':
      return applyAssistantMessage(items, event, data)
    case 'assistant_delta':
      return applyAssistantDelta(items, data)
    case 'reasoning_delta':
      return applyReasoningDelta(items, event, data)
    case 'tool_call_started':
    case 'tool_call_finished':
    case 'tool_call_denied':
    case 'tool_result_message':
      return applyToolEvent(items, event)
    default:
      return items
  }
}

/** Run failure: append an error item in place (deduped by entry_id, so a reload adds no extra one);
 *  it is the run's only output and must stay visible. */
function applyRunFailed(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  // `text` is the kernel's display sentence (identical to the persisted entry);
  // `message` is the raw cause, used as fallback
  const message = str(data.text) || str(data.message)
  if (!message) return items
  const entryId = str(data.entry_id) || null
  if (entryId !== null && items.some((item) => item.kind === 'error' && item.entryId === entryId)) return items
  return [...items, { kind: 'error', entryId, text: message }]
}

function applyUserMessage(items: TimelineItem[], event: TimelineEvent, data: Record<string, unknown>): TimelineItem[] {
  const entryId = str(data.entry_id)
  const message = data.message as MessagePayload | undefined
  const { text, images } = userContent(message?.content, entryId || null)
  if (!entryId || (!text && images.length === 0)) return items
  if (items.some((item) => item.kind === 'user' && item.entryId === entryId)) return items
  const next: TimelineItem = {
    kind: 'user',
    entryId,
    text,
    ...(images.length ? { images } : {}),
    ts: event.ts,
  }
  // Fold the optimistic bubble in place instead of adding another; images pair by text (the
  // event carries no client id) and pending inputs by input_id.
  const inputId = str(data.input_id)
  if (inputId) {
    const at = items.findIndex(
      (item) => item.kind === 'user' && item.pending?.inputId === inputId,
    )
    if (at >= 0) return replace(items, at, next)
  }
  const last = items.at(-1)
  if (last?.kind === 'user' && last.entryId === null && last.text === text) {
    return replace(items, items.length - 1, next)
  }
  return [...items, next]
}

function applyAssistantMessage(
  items: TimelineItem[],
  event: TimelineEvent,
  data: Record<string, unknown>,
): TimelineItem[] {
  const entryId = str(data.entry_id)
  const message = data.message as MessagePayload | undefined
  if (!entryId) return items
  if (items.some((item) => item.kind === 'assistant' && item.entryId === entryId)) return items
  const text = messageContent(message?.content)
  let out = items
  const streaming = lastIndexOf(out, (item) => item.kind === 'assistant' && item.streaming)
  if (streaming >= 0) {
    // Commit in place: the persisted message is this streaming item (deltas are lost
    // on replay, so durable wins).
    const item = out[streaming]
    if (item?.kind === 'assistant') {
      out = replace(out, streaming, { ...item, entryId, text: text || item.text, streaming: false, ts: event.ts })
    }
  } else if (text.trim()) {
    out = [...out, { kind: 'assistant', entryId, text, streaming: false, ts: event.ts }]
  }
  out = closeReasoning(out)
  for (const call of message?.tool_calls ?? []) {
    const callId = str(call.id)
    const name = str(call.function?.name)
    if (!callId || !name) continue
    if (indexOfTool(out, callId) >= 0) continue
    out = [...out, emptyTool(callId, name, str(call.function?.arguments))]
  }
  return out
}

function applyAssistantDelta(items: TimelineItem[], data: Record<string, unknown>): TimelineItem[] {
  const text = str(data.text)
  if (!text) return items
  const out = closeReasoning(items)
  const at = lastIndexOf(out, (item) => item.kind === 'assistant' && item.streaming)
  const item = at >= 0 ? out[at] : undefined
  if (item?.kind === 'assistant') return replace(out, at, { ...item, text: item.text + text })
  // Reading, text and identity all come from the persisted message.
  return [...out, { kind: 'assistant', entryId: null, text, streaming: true, ts: null }]
}

function applyReasoningDelta(
  items: TimelineItem[],
  event: TimelineEvent,
  data: Record<string, unknown>,
): TimelineItem[] {
  const text = str(data.text)
  if (!text) return items
  // A reasoning delta extends only the reasoning item at the very tail;
  // text or tools in between start a new one.
  const at = items.length - 1
  const item = items[at]
  if (item?.kind === 'reasoning' && item.streaming) {
    return replace(items, at, { ...item, text: item.text + text, endedAt: event.ts })
  }
  return [...items, { kind: 'reasoning', text, startedAt: event.ts, endedAt: event.ts, streaming: true }]
}

function applyToolEvent(raw: TimelineItem[], event: TimelineEvent): TimelineItem[] {
  const items = closeReasoning(raw)
  const data = event.data ?? {}
  if (event.type === 'tool_result_message') {
    const message = data.message as MessagePayload | undefined
    const callId = str(message?.tool_call_id)
    const at = indexOfTool(items, callId)
    if (at < 0) return items
    const content = messageContent(message?.content)
    const item = items[at]
    if (item?.kind !== 'tool') return items
    return replace(items, at, {
      ...item,
      result: content,
      status: item.status === 'running' ? classifyToolResult(content) : item.status,
    })
  }

  const callId = str(data.tool_call_id)
  if (!callId) return items
  const at = indexOfTool(items, callId)
  if (event.type === 'tool_call_started') {
    if (at < 0) {
      const args = data.arguments === undefined ? '' : JSON.stringify(data.arguments)
      return [...items, emptyTool(callId, str(data.tool), args)]
    }
    const item = items[at]
    if (item?.kind !== 'tool' || item.status !== 'running') return items
    // Arguments come from the assistant message; fill only when missing
    // (re-serializing changes key order).
    return replace(items, at, { ...item, name: item.name || str(data.tool), args: item.args || JSON.stringify(data.arguments ?? {}) })
  }

  if (at < 0) return items
  const item = items[at]
  if (item?.kind !== 'tool') return items
  const status: ToolStatus =
    event.type === 'tool_call_denied' ? 'failed' : str(data.status) === 'failed' || str(data.status) === 'denied' ? 'failed' : 'ok'
  const durationMs = typeof data.duration_ms === 'number' ? data.duration_ms : item.durationMs
  const result = typeof data.content === 'string' ? data.content : item.result
  return replace(items, at, closeRunStreams({ ...item, status, durationMs, result }))
}

// ---------------------------------------------------------------- history + run merge

/** Merge this run's items back into session history: duplicate persisted items update in place
 *  (the live reading is fuller), live-only items return to their anchor. */
export function mergeItems(history: TimelineItem[], run: TimelineItem[]): TimelineItem[] {
  const at = new Map<string, number>()
  const out = history.map((item, index) => {
    const key = identity(item)
    if (key !== null) at.set(key, index)
    return item
  })
  const before = new Map<number, TimelineItem[]>()
  const after = new Map<number, TimelineItem[]>()
  const tail: TimelineItem[] = []

  run.forEach((item, index) => {
    const key = identity(item)
    const known = key === null ? undefined : at.get(key)
    if (known !== undefined) {
      out[known] = overlay(out[known]!, item)
      return
    }
    // Anchor: the nearest persisted item before it (insert after), else the nearest one after
    // it (insert before), else append at the tail.
    let anchor = -1
    for (let back = index - 1; back >= 0; back -= 1) {
      const backKey = identity(run[back]!)
      if (backKey !== null && at.has(backKey)) {
        anchor = at.get(backKey)!
        break
      }
    }
    if (anchor >= 0) {
      after.set(anchor, [...(after.get(anchor) ?? []), item])
      return
    }
    let ahead = -1
    for (let next = index + 1; next < run.length; next += 1) {
      const nextKey = identity(run[next]!)
      if (nextKey !== null && at.has(nextKey)) {
        ahead = at.get(nextKey)!
        break
      }
    }
    if (ahead >= 0) {
      before.set(ahead, [...(before.get(ahead) ?? []), item])
      return
    }
    tail.push(item)
  })

  const merged: TimelineItem[] = []
  out.forEach((item, index) => {
    merged.push(...(before.get(index) ?? []), item, ...(after.get(index) ?? []))
  })
  return [...merged, ...tail]
}

/** Two copies of one persisted item: position and content come from history, live readings
 *  (status, duration, sub-steps) overlay on top. */
function overlay(base: TimelineItem, run: TimelineItem): TimelineItem {
  if (base.kind !== 'tool' || run.kind !== 'tool') return base
  const rank: Record<ToolStatus, number> = { running: 1, ok: 2, failed: 2 }
  return {
    ...base,
    status: rank[run.status] > rank[base.status] ? run.status : base.status,
    result: run.result ?? base.result,
    durationMs: run.durationMs ?? base.durationMs,
    // Details are live-only: history (reload) lacks them, the run has them — whoever has them wins
    runs: run.runs.some((item) => item.items.length > 0) ? run.runs : base.runs,
  }
}

// ---------------------------------------------------------------- one turn: grouping and folding

/**
 * One turn (everything between two user messages) plus the material for its folding decision.
 * Only the process folds: a finished turn keeps its closing text and collapses the process into one
 * row; the decision lives here so the live and settled views fold into the same shape.
 */
export type TurnGroup = {
  /** What the user said this turn; null = window starts mid-turn (earlier history not loaded). */
  user: UserItem | null
  /** All items of the turn, order preserved (copying a whole turn uses this order). */
  items: TimelineItem[]
  /** Collapsible process: everything between the user item and the closing text. */
  process: TimelineItem[]
  /** Closing text: the turn's persisted last assistant item; null = no closing, no fold. */
  answer: AnswerItem | null
  /** Turn duration (answer minus user reading); null if a reading is missing or time went back. */
  durationMs: number | null
  /** Group start in the flat list; it keeps live-only keys globally unique. */
  offset: number
  /** Fold-toggle identity: from the user item (or the answer for a half turn); it never changes. */
  key: string
}

type UserItem = Extract<TimelineItem, { kind: 'user' }>
type AnswerItem = Extract<TimelineItem, { kind: 'assistant' }>

/** Closing text must be the last item: trailing tools mean an interruption or failure,
 *  so the turn stays flat. */
function isAnswer(item: TimelineItem): item is AnswerItem {
  return item.kind === 'assistant' && item.entryId !== null && item.text.trim() !== ''
}

/** Items → turn groups; split points are user items: each opens a turn until the next one. */
export function turnGroups(items: TimelineItem[]): TurnGroup[] {
  const groups: TurnGroup[] = []
  let start = 0
  for (let end = 0; end <= items.length; end += 1) {
    if (end < items.length && items[end]!.kind !== 'user') continue
    const slice = items.slice(start, end)
    if (slice.length > 0) groups.push(makeGroup(slice, start))
    start = end
  }
  return groups
}

function makeGroup(items: TimelineItem[], offset: number): TurnGroup {
  const head = items[0]
  const user = head?.kind === 'user' ? head : null
  const tail = items.at(-1)
  const answer = tail !== undefined && isAnswer(tail) ? tail : null
  const from = user === null ? 0 : 1
  const to = answer === null ? items.length : items.length - 1
  const span = user !== null && user.ts !== null && answer !== null && answer.ts !== null ? answer.ts - user.ts : null
  return {
    user,
    items,
    process: items.slice(from, to),
    answer,
    durationMs: span !== null && span >= 0 ? span : null,
    offset,
    key: identity(user ?? answer ?? items[0]!) ?? `turn:${offset}`,
  }
}

// ---------------------------------------------------------------- sub-runs: card rows and panel

/** The card's collapsed rows: each sub-run's tool items flattened by task, in call order. */
export function subagentSteps(item: TimelineItem): SubagentStep[] {
  if (item.kind !== 'tool') return []
  const steps: SubagentStep[] = []
  for (const run of item.runs) {
    for (const child of run.items) {
      if (child.kind === 'tool') {
        steps.push({ task: run.task, callId: child.callId, name: child.name, args: child.args, status: child.status })
      }
    }
  }
  return steps
}

/** One sub-run as the panel wants it (flat row). */
export type SubagentRunView = {
  /** The subagent call that dispatched it; the panel's key is this + index. */
  callId: string
  task: string
  index: number
  items: TimelineItem[]
  /** Batch still running (the panel's marker); per-run success is only in the kernel summary. */
  running: boolean
}

/**
 * Every sub-run in the timeline, flattened in order — the panel reads this.
 * Details (`items`) are not persisted: a reloaded history shows task names with empty details.
 */
export function subagentRuns(items: TimelineItem[]): SubagentRunView[] {
  const views: SubagentRunView[] = []
  for (const item of items) {
    if (item.kind !== 'tool' || item.name !== 'subagent') continue
    for (const run of item.runs) {
      views.push({
        callId: item.callId,
        task: run.task,
        index: run.index,
        items: run.items,
        running: item.status === 'running',
      })
    }
  }
  return views
}
