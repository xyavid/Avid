/**
 * 活动域的收敛：**纯函数**，可脱离 UI 单测（`events/__tests__/reducer.test.ts`）。
 *
 * 两条不变量写在这里：
 *   · **I12 乱序收敛**：durable 事件渲染前 flush 待处理 delta；终止事件渲染前
 *     cancel 待处理 delta。两者不混用——flush 是把已有内容落上去，cancel 是别让
 *     旧内容盖住最终结果。因此 delta 全丢也不影响正确性（I5：durable 带完整内容）。
 *   · **幂等**：按 `(run_id, seq)` 去重，重放与重复消费都不改变最终状态。
 *
 * delta 的可见性：`applyDelta` **立刻**把增量合成成乐观条目（不等 durable），
 * 否则纯文本回答期间界面在整轮结束前完全不动。乐观条目在 durable 消息到达时
 * 被就地替换、在终止事件时被丢弃。
 *
 * 权威视图来自条目（服务端持久层），这里只维护「已知历史 + 正在到达的步骤」。
 */

import type { EventEnvelope, MessagePayload, ToolCallPayload } from './types'
import type { Entry } from '../api/types'
import type {
  ApprovalRequest,
  CompactionNote,
  TimelineEntry,
  ToolCallRef,
  ToolRun,
  ToolStatus,
} from '../lib/timeline'

export type RunPhase =
  | 'idle'
  | 'submitting'
  | 'streaming'
  | 'awaiting_approval'
  | 'cancelling'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface RunView {
  runId: string | null
  sessionId: string | null
  phase: RunPhase
  /** 已知最大 durable seq = 重连游标。 */
  seq: number
  round: number
  tokens: number
  activity: string
  entries: TimelineEntry[]
  tools: ToolRun[]
  approvals: ApprovalRequest[]
  compactions: CompactionNote[]
  deltaText: string
  error: { code: string; message: string } | null
  cancelRequested: boolean
  cancelReason: string | null
  /** 视图待重建：收到 resync 或解析失败后为 true，重建完成时清掉。 */
  detached: boolean
  startedAt: number | null
  finishedAt: number | null
  lastEventAt: number
}

export function emptyView(sessionId: string | null = null): RunView {
  return {
    runId: null,
    sessionId,
    phase: 'idle',
    seq: 0,
    round: 0,
    tokens: 0,
    activity: '',
    entries: [],
    tools: [],
    approvals: [],
    compactions: [],
    deltaText: '',
    error: null,
    cancelRequested: false,
    cancelReason: null,
    detached: false,
    startedAt: null,
    finishedAt: null,
    lastEventAt: 0,
  }
}

/** delta 落上去：合成/更新一个乐观的 assistant 条目。 */
export function flushDelta(view: RunView): RunView {
  if (!view.deltaText) return view
  const id = `delta:${view.runId ?? 'pending'}`
  const existing = view.entries.find((entry) => entry.id === id)
  const entries = existing
    ? view.entries.map((entry) =>
        entry.id === id ? { ...entry, text: entry.text + view.deltaText } : entry,
      )
    : [
        ...view.entries,
        {
          id,
          kind: 'assistant' as const,
          text: view.deltaText,
          seq: view.seq,
          at: view.lastEventAt,
          optimistic: true,
        },
      ]
  return { ...view, entries, deltaText: '' }
}

/** delta 丢掉：终止类事件到达时用，避免旧内容盖住最终结果。 */
export function cancelDelta(view: RunView): RunView {
  // 乐观条目与 deltaText 都要清。applyDelta 已经把 delta 落进 entries，
  // 只判 deltaText 会漏掉那些条目——终止事件后它们会继续显示旧内容。
  if (!view.deltaText && !view.entries.some((entry) => entry.optimistic)) return view
  return { ...view, deltaText: '', entries: view.entries.filter((e) => !e.optimistic) }
}

export function applyDelta(view: RunView, text: string): RunView {
  if (!text) return view
  // **立刻**合成乐观条目，而不是攒到下一个 durable 事件：纯文本回答期间一个
  // durable 事件都没有，攒着等于整轮结束时才一次性出现——delta 的流式收益为 0
  // （首屏可见 token 的目标也就不可达）。`flushDelta` 仍是唯一的合成点。
  return flushDelta({ ...view, deltaText: view.deltaText + text })
}

function toolCallsOf(message: MessagePayload | undefined): ToolCallRef[] {
  const calls: ToolCallPayload[] = message?.tool_calls ?? []
  return calls.map((call) => {
    let parsed: Record<string, unknown> = {}
    try {
      const value = call.function?.arguments
      parsed = value ? (JSON.parse(value) as Record<string, unknown>) : {}
    } catch {
      parsed = {}
    }
    return { toolCallId: call.id, tool: call.function?.name ?? '', arguments: parsed }
  })
}

function messageEntry(event: EventEnvelope, kind: TimelineEntry['kind']): TimelineEntry {
  const message = event.data.message
  return {
    id: event.data.entry_id ?? `${event.run_id}:${event.seq}`,
    kind,
    text: typeof message?.content === 'string' ? message.content : '',
    entryId: event.data.entry_id,
    toolCallId: message?.tool_call_id,
    toolCalls: kind === 'assistant' ? toolCallsOf(message) : undefined,
    seq: event.seq ?? 0,
    at: event.ts,
  }
}

function upsertTool(view: RunView, run: ToolRun): RunView {
  const found = view.tools.some((item) => item.toolCallId === run.toolCallId)
  return {
    ...view,
    tools: found
      ? view.tools.map((item) => (item.toolCallId === run.toolCallId ? { ...item, ...run } : item))
      : [...view.tools, run],
  }
}

function noticeEntry(event: EventEnvelope, notice: TimelineEntry['notice'], text: string): TimelineEntry {
  return {
    id: `notice:${event.type}:${event.seq}`,
    kind: 'notice',
    notice,
    text,
    seq: event.seq ?? 0,
    at: event.ts,
  }
}

/** 事件 → 新状态。不修改入参；同值事件返回原引用（免重渲染）。 */
export function applyEvent(view: RunView, event: EventEnvelope): RunView {
  if (event.seq !== null && event.seq <= view.seq) return view

  let next = view
  if (event.type === 'run_finished' || event.type === 'run_failed' || event.type === 'run_cancelled') {
    next = cancelDelta(next)
  } else if (event.seq !== null) {
    next = flushDelta(next)
  }

  next = {
    ...next,
    runId: event.run_id || next.runId,
    sessionId: event.session_id || next.sessionId,
    seq: event.seq ?? next.seq,
    lastEventAt: event.ts,
  }

  switch (event.type) {
    case 'run_started':
      return {
        ...next,
        phase: 'streaming',
        startedAt: event.ts,
        error: null,
        detached: false,
      }
    case 'user_message':
    case 'assistant_message':
    case 'tool_result_message': {
      const kind =
        event.type === 'user_message' ? 'user' : event.type === 'assistant_message' ? 'assistant' : 'tool'
      const cleaned = kind === 'assistant' ? next.entries.filter((e) => !e.optimistic) : next.entries
      const entry = messageEntry(event, kind)
      const entries = [...cleaned]
      const duplicate = entries.findIndex((item) => item.id === entry.id)
      if (duplicate >= 0) entries[duplicate] = entry
      else entries.push(entry)
      return { ...next, entries }
    }
    case 'tool_call_started':
      return upsertTool(next, {
        toolCallId: String(event.data.tool_call_id ?? ''),
        tool: String(event.data.tool ?? ''),
        arguments: (event.data.arguments as Record<string, unknown>) ?? {},
        status: 'running',
        truncated: false,
        contentChars: 0,
        durationMs: 0,
        seq: event.seq ?? 0,
        at: event.ts,
      })
    case 'tool_call_finished':
      return upsertTool(next, {
        toolCallId: String(event.data.tool_call_id ?? ''),
        tool: String(event.data.tool ?? ''),
        arguments: (event.data.arguments as Record<string, unknown>) ?? {},
        status: (event.data.status as ToolStatus) ?? 'ok',
        truncated: Boolean(event.data.truncated),
        contentChars: Number(event.data.content_chars ?? 0),
        durationMs: Number(event.data.duration_ms ?? 0),
        seq: event.seq ?? 0,
        at: event.ts,
      })
    case 'tool_call_denied':
      return upsertTool(next, {
        toolCallId: String(event.data.tool_call_id ?? ''),
        tool: String(event.data.tool ?? ''),
        arguments: (event.data.arguments as Record<string, unknown>) ?? {},
        status: 'denied',
        truncated: false,
        contentChars: 0,
        durationMs: 0,
        reason: String(event.data.reason ?? ''),
        seq: event.seq ?? 0,
        at: event.ts,
      })
    case 'approval_requested':
      return {
        ...next,
        phase: 'awaiting_approval',
        approvals: [
          ...next.approvals,
          {
            approvalId: String(event.data.approval_id ?? ''),
            tool: String(event.data.tool ?? ''),
            arguments: (event.data.arguments as Record<string, unknown>) ?? {},
            reason: String(event.data.reason ?? ''),
            createdAt: Number(event.data.created_at ?? event.ts),
            expiresAt: Number(event.data.expires_at ?? 0),
            decision: null,
            resolvedReason: null,
          },
        ],
      }
    case 'approval_resolved': {
      const id = String(event.data.approval_id ?? '')
      return {
        ...next,
        phase: next.phase === 'awaiting_approval' ? 'streaming' : next.phase,
        approvals: next.approvals.map((item) =>
          item.approvalId === id
            ? {
                ...item,
                decision: String(event.data.decision ?? 'deny'),
                resolvedReason: String(event.data.reason ?? ''),
              }
            : item,
        ),
      }
    }
    case 'context_compacted': {
      const note = {
        id: `compact:${event.seq}`,
        step: String(event.data.step ?? ''),
        detail: String(event.data.detail ?? ''),
        before: Number(event.data.before ?? 0),
        after: Number(event.data.after ?? 0),
        seq: event.seq ?? 0,
      }
      return {
        ...next,
        compactions: [...next.compactions, note],
        entries: [
          ...next.entries,
          noticeEntry(event, 'compaction', `${note.step} — ${note.detail}`),
        ],
      }
    }
    case 'todo_reminder':
      return { ...next, entries: [...next.entries, noticeEntry(event, 'todo', String(event.data.content ?? ''))] }
    case 'stop_nudge':
      return { ...next, entries: [...next.entries, noticeEntry(event, 'nudge', String(event.data.content ?? ''))] }
    case 'run_status':
      return {
        ...next,
        round: Number(event.data.round ?? next.round),
        tokens: Number(event.data.tokens ?? next.tokens),
        activity: String(event.data.activity ?? next.activity),
      }
    case 'run_finished':
      return {
        ...next,
        phase: 'done',
        finishedAt: event.ts,
        tokens: Number(event.data.tokens ?? next.tokens),
      }
    case 'run_failed':
      return {
        ...next,
        phase: 'failed',
        finishedAt: event.ts,
        error: {
          code: String(event.data.code ?? 'internal'),
          message: String(event.data.message ?? ''),
        },
      }
    case 'run_cancelled':
      return {
        ...next,
        phase: 'cancelled',
        finishedAt: event.ts,
        cancelReason: String(event.data.reason ?? ''),
      }
    case 'resync':
      return { ...next, detached: true }
    case 'assistant_delta':
      return applyDelta(next, String(event.data.text ?? ''))
    default:
      return next
  }
}

/**
 * 历史里的工具状态推断。
 *
 * 只在「没有事件可依赖」时用（刷新后从条目重建）：此时拿不到服务端的
 * `tool_call_finished.status`，而条目本身只有内容。规则与
 * `src/avid/web/schemas.py::classify_tool_status` 一一对应——那是线格式的判定点，
 * 这里是它的**离线镜像**；两者一旦分叉，以服务端为准（事件到达时会覆盖）。
 */
export function inferToolStatus(content: string): ToolStatus {
  if (content.startsWith('Permission denied.')) return 'denied'
  if (content.startsWith('错误：') || content.slice(0, 64).includes('执行失败：')) return 'failed'
  if (content.includes('按上下文预算截断')) return 'truncated'
  return 'ok'
}

/** 用条目重建视图（刷新、resync、断线对账都走它：权威视图来自条目）。 */
export function viewFromEntries(view: RunView, entries: Entry[]): RunView {
  const ordered = [...entries].sort((a, b) => a.seq - b.seq)
  const timeline: TimelineEntry[] = []
  const tools: ToolRun[] = []
  const byToolCall = new Map<string, ToolRun>()

  for (const entry of ordered) {
    if (entry.type !== 'message' || !entry.message) continue
    const message = entry.message as unknown as MessagePayload
    const kind: TimelineEntry['kind'] =
      message.role === 'user' ? 'user' : message.role === 'assistant' ? 'assistant' : 'tool'
    const text = typeof message.content === 'string' ? message.content : ''

    if (kind === 'tool') {
      // 工具结果不是独立条目，它挂在声明的工具卡上（内容进 contents 映射）。
      const toolCallId = String(message.tool_call_id ?? '')
      const declared = byToolCall.get(toolCallId)
      const status = inferToolStatus(text)
      if (declared) {
        declared.status = status
        declared.contentChars = text.length
        declared.truncated = status === 'truncated'
      } else {
        tools.push({
          toolCallId,
          tool: '',
          arguments: {},
          status,
          truncated: status === 'truncated',
          contentChars: text.length,
          durationMs: 0,
          seq: entry.seq,
          at: entry.timestamp,
        })
      }
    }

    const refs = kind === 'assistant' ? toolCallsOf(message) : []
    for (const ref of refs) {
      const run: ToolRun = {
        toolCallId: ref.toolCallId,
        tool: ref.tool,
        arguments: ref.arguments,
        status: 'ok',
        truncated: false,
        contentChars: 0,
        durationMs: 0,
        seq: entry.seq,
        at: entry.timestamp,
      }
      tools.push(run)
      byToolCall.set(run.toolCallId, run)
    }

    timeline.push({
      id: entry.entry_id,
      kind,
      text,
      entryId: entry.entry_id,
      toolCallId: message.tool_call_id,
      toolCalls: kind === 'assistant' ? refs : undefined,
      seq: entry.seq,
      at: entry.timestamp,
    })
  }

  return { ...view, entries: timeline, tools, detached: false }
}
