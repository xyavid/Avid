/**
 * 与 `src/avid/runtime/events.py` 对齐的事件联合类型。
 *
 * 这个文件是前端侧事件名的**单点**：`tests/test_event_contract.py` 会解析
 * EVENTS:BEGIN / EVENTS:END 之间的成员集合，与内核的 EVENT_TYPES 做集合相等断言。
 * 加一个事件要同时改两侧，否则测试失败——这就是不上生成器时代的漂移检查。
 */

import type { PermissionMode } from '../api/types'

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
  | 'todo_reminder'
  | 'stop_nudge'
  | 'run_finished'
  | 'run_failed'
  | 'run_cancelled'
  | 'resync'
  | 'run_status'
  | 'assistant_delta'
// EVENTS:END

/** durable：带 id/seq、可重放。重连补齐的粒度就是它。 */
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
  'todo_reminder',
  'stop_nudge',
  'run_finished',
  'run_failed',
  'run_cancelled',
  'resync',
]

/** transient：不带 id，状态类，断了就断了。 */
export const TRANSIENT_EVENT_TYPES: readonly AvidEventType[] = ['run_status']

/** delta：不带 id，可任意丢；默认不投递，需 `?deltas=1` 显式订阅。 */
export const DELTA_EVENT_TYPES: readonly AvidEventType[] = ['assistant_delta']

/** 终态事件：渲染前必须 cancel 待处理 delta（不变量 I12）。 */
export const TERMINAL_EVENT_TYPES: readonly AvidEventType[] = [
  'run_finished',
  'run_failed',
  'run_cancelled',
]

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

/** 一条 SSE 帧解出来的信封。字段名与内核一致（不做驼峰转换以外的重命名）。 */
export interface EventEnvelope {
  run_id: string
  session_id: string
  seq: number | null
  ts: number
  type: AvidEventType
  data: EventData
}

/** 各事件的 data：只声明前端真正读的字段，其余按需要时再加。 */
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
  activity?: string
  finish_reason?: string
  text?: string
  code?: string
  after_seq?: number
  // run_started 带归属与权限模式（阶段 18）：刷新页面后重建界面靠它。
  workspace?: string
  workspace_root?: string
  permission?: PermissionMode
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
