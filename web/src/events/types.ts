/**
 * Event union mirroring `avid/agent/events.py`; this file is the frontend's single source for
 * event names. `tests/test_event_contract.py` parses the EVENTS:BEGIN/EVENTS:END block below and
 * asserts set equality with the kernel's EVENT_TYPES, so an event must be added on both sides.
 */

import type { SandboxState, UsageReport, WorktreeFacts } from '../api/types'

/**
 * Permission mode reported by run_started: `normal` prompts once for destructive commands;
 * `full` needs the explicit full_access_ack and runs without prompts or sandbox.
 */
export type RunPermission = 'normal' | 'full'

// EVENTS:BEGIN
export type AvidEventType =
  | 'run_started'
  | 'user_message'
  | 'assistant_message'
  | 'tool_result_message'
  | 'tool_call_started'
  | 'tool_call_finished'
  | 'tool_call_denied'
  | 'approval_requested'
  | 'approval_resolved'
  | 'context_compacted'
  | 'stop_nudge'
  | 'run_finished'
  | 'run_failed'
  | 'run_cancelled'
  | 'resync'
  | 'run_status'
  | 'assistant_delta'
  | 'reasoning_delta'
// EVENTS:END

/** Durable: carries id/seq and replays; this is the granularity of reconnect backfill. */
export const DURABLE_EVENT_TYPES: readonly AvidEventType[] = [
  'run_started',
  'user_message',
  'assistant_message',
  'tool_result_message',
  'tool_call_started',
  'tool_call_finished',
  'tool_call_denied',
  'approval_requested',
  'approval_resolved',
  'context_compacted',
  'stop_nudge',
  'run_finished',
  'run_failed',
  'run_cancelled',
  'resync',
]

/** Transient: no id, state-like, dropped with the connection. */
export const TRANSIENT_EVENT_TYPES: readonly AvidEventType[] = ['run_status']

/** Delta: no id, lossy; not delivered unless the stream is opened with `?deltas=1`. */
export const DELTA_EVENT_TYPES: readonly AvidEventType[] = [
  'assistant_delta',
  // Chain-of-thought stream, kept separate from assistant_delta content.
  'reasoning_delta',
]

/** Terminal events: pending deltas must be cancelled before rendering. */
export const TERMINAL_EVENT_TYPES: readonly AvidEventType[] = [
  'run_finished',
  'run_failed',
  'run_cancelled',
]

/**
 * Payload fields the frontend reads, per event type — the event half of the contract whose REST half
 * is `tests/test_wire_contract.py`. `tests/test_event_payload_contract.py` runs a scripted session
 * and fails when the kernel stops sending a field declared here, so a rename cannot degrade the UI
 * silently. A field the frontend reads only defensively (falling back when it is absent) is
 * deliberately not declared, because its absence is not a defect.
 */
// EVENT_FIELDS:BEGIN
export interface EventPayloadFields {
  run_started: 'permission'
  user_message: 'entry_id' | 'message'
  assistant_message: 'entry_id' | 'message'
  tool_result_message: 'entry_id' | 'message'
  tool_call_started: 'tool_call_id'
  tool_call_finished: 'tool_call_id'
  tool_call_denied: 'tool_call_id'
  /** The tool-approval variant; a question carries `kind` and `options` on top. */
  approval_requested: 'approval_id'
  approval_resolved: 'approval_id'
  context_compacted: never
  stop_nudge: never
  run_finished: 'usage'
  run_failed: 'text' | 'message' | 'entry_id' | 'usage'
  run_cancelled: 'usage'
  resync: never
  run_status: 'usage'
  assistant_delta: 'text'
  reasoning_delta: 'text'
}
// EVENT_FIELDS:END

export interface MessagePayload {
  role: 'user' | 'assistant' | 'tool'
  content?: string | null
  tool_calls?: ToolCallPayload[]
  tool_call_id?: string
}

export interface ToolCallPayload {
  id: string
  type?: string
  function?: { name?: string; arguments?: string }
}

/** Envelope of one SSE frame; field names match the kernel (no renaming beyond camelCase). */
export interface EventEnvelope {
  run_id: string
  session_id: string
  seq: number | null
  ts: number
  type: AvidEventType
  data: EventData
}

/** Per-event data: only fields the frontend actually reads are declared. */
export interface EventData {
  [key: string]: unknown
  prompt?: string
  auto_approve?: boolean
  message?: MessagePayload
  entry_id?: string
  tool?: string
  tool_call_id?: string
  arguments?: Record<string, unknown>
  content?: string
  content_chars?: number
  truncated?: boolean
  duration_ms?: number
  round?: number
  status?: string
  kind?: string
  reason?: string
  decision?: string
  approval_id?: string
  expires_at?: number
  created_at?: number
  step?: string
  detail?: string
  before?: number
  after?: number
  tokens?: number
  /** Unified usage snapshot: on every round's run_status and on terminal run_finished. */
  usage?: UsageReport
  activity?: string
  finish_reason?: string
  text?: string
  code?: string
  after_seq?: number
  // `permission` only says "full access?"; the actual sandbox form is in the sandbox_* fields.
  workspace?: string
  workspace_root?: string
  /** The root this run works on (`workspace_root`) and the tree it is, when it has one. */
  worktree?: Pick<WorktreeFacts, 'path' | 'branch' | 'base' | 'reason'> | null
  permission?: RunPermission
  sandbox_state?: SandboxState
  sandbox_notes?: string[]
  /** Subagent child-run marker: live-only, folded into the subagent card by state/timeline.ts. */
  subagent?: { task: string; index: number }
}

export function isDurable(event: EventEnvelope): boolean {
  return event.seq !== null && DURABLE_EVENT_TYPES.includes(event.type)
}

export function isTerminal(event: EventEnvelope): boolean {
  return TERMINAL_EVENT_TYPES.includes(event.type)
}

export function isDelta(event: EventEnvelope): boolean {
  return DELTA_EVENT_TYPES.includes(event.type)
}
