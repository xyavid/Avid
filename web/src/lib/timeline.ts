/**
 * 时间线渲染用的视图类型：**只描述形状，不含逻辑**。
 *
 * 为什么放在 L0/L1（`lib/`）而不是 `events/reducer`：`ui/patterns` 是"只接受
 * props、不读 store、不发请求"的一层，它 import `events/`（活动域）或 `state/`
 * （界面域）就是 L1 反向依赖上层——`frontend-architecture.md` §3.4 与
 * `ui/patterns/README.md` 都声明了相反的方向，而声明必须有单一来源可依。
 *
 * 类型下沉之后：`events/reducer`（生产这些视图）与 `ui/patterns`（渲染它们）
 * 依赖同一份形状，且只有一条 import 路径（不做 re-export，避免两个入口）。
 */

export type ToolStatus = 'running' | 'ok' | 'failed' | 'denied' | 'truncated'

export interface ToolCallRef {
  toolCallId: string
  tool: string
  arguments: Record<string, unknown>
}

export interface TimelineEntry {
  id: string
  kind: 'user' | 'assistant' | 'tool' | 'notice'
  notice?: 'compaction' | 'todo' | 'nudge'
  text: string
  entryId?: string
  toolCallId?: string
  toolCalls?: ToolCallRef[]
  seq: number
  at: number
  /** 乐观渲染的临时条目：durable 消息到达时就地丢弃（I11）。 */
  optimistic?: boolean
}

export interface ToolRun {
  toolCallId: string
  tool: string
  arguments: Record<string, unknown>
  status: ToolStatus
  truncated: boolean
  contentChars: number
  durationMs: number
  reason?: string
  seq: number
  at: number
}

export interface ApprovalRequest {
  approvalId: string
  tool: string
  arguments: Record<string, unknown>
  reason: string
  createdAt: number
  expiresAt: number
  decision: string | null
  resolvedReason: string | null
}

export interface CompactionNote {
  id: string
  step: string
  detail: string
  before: number
  after: number
  seq: number
}
