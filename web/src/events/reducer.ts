/**
 * 事件 → 视图：**纯函数**状态机（无 DOM、无 fetch、不读时钟）。
 *
 * 全部用例都能在默认 node 环境跑，因为收敛性只由"事件序列 + 现有视图"决定。
 *
 * ## 三条不变量
 *
 * · **I12 flush**：durable 事件渲染前先把待处理的 delta 落成普通条目
 *   （`flushDelta`），终态事件更是必须先 flush，否则最后一段正文会永远带着
 *   `streaming` 标记、界面以为还在生成。这与 `docs/guide/web-ui.md` §3 一致：
 *   flush 是"把已有内容落上去"，与 delta 通道的"可任意丢"配合才成立（durable 带全量）。
 * · **幂等**：SSE 重连会把 durable 事件整段重发，所以同一事件重复到达必须收敛到同一
 *   结果。去重键按事件的**身份**取：消息用 `entry_id`、工具用 `tool_call_id`、
 *   审批用 `approval_id`、提醒用 `type + seq`。`seq <= view.seq` 的快路径只是省一次
 *   重渲染，幂等不依赖它（换了 seq 的同一条 `entry_id` 也不会产生第二条）。
 * · **纯函数**：不改入参；只返回新对象。
 *
 * ## delta 的可见性
 *
 * `assistant_delta` **立刻**合成一个 `streaming` 条目（不等 durable），否则纯文本回答
 * 期间界面在整轮结束前完全不动。思维链增量（`reasoning_delta`）走独立的流式条目，
 * 排在正文之前——它是过程，不是回复。
 *
 * 权威视图仍来自条目（服务端持久层）：`viewFromEntries` 是刷新 / resync 后的重建路径，
 * 与事件路径同形。
 */

import type { Approval, Entry, UsageReport } from '../api/types'
import type { EventEnvelope, MessagePayload, ToolCallPayload } from './types'

export type RunPhase = 'idle' | 'running' | 'awaiting_approval' | 'finished' | 'failed' | 'cancelled'

export interface TimelineEntry {
  id: string
  kind: 'user' | 'assistant' | 'tool' | 'notice'
  ts: number
  text: string
  /** assistant 流式累积中 */
  streaming?: boolean
  /** tool 条目 */
  tool?: string
  toolCallId?: string
  args?: Record<string, unknown>
  status?: 'running' | 'ok' | 'error' | 'denied'
  durationMs?: number
  /** notice 条目的语义档 */
  notice?: 'compaction' | 'todo' | 'nudge' | 'info'
}

export interface ToolRun {
  toolCallId: string
  tool: string
  args: Record<string, unknown>
  status: 'running' | 'ok' | 'error' | 'denied'
  resultText: string
  durationMs: number | null
  startedAt: number
  subagent?: { task: string; index: number }
}

export interface CompactionNote {
  ts: number
  step: string
  before: number
  after: number
}

export interface RunView {
  sessionId: string | null
  runId: string | null
  phase: RunPhase
  seq: number
  round: number
  tokens: number
  usage: UsageReport | null
  activity: string
  entries: TimelineEntry[]
  tools: ToolRun[]
  approvals: Approval[]
  compactions: CompactionNote[]
  error: { code: string; message: string } | null
  /** 重连补齐后为 true：界面可以提示"历史已重放" */
  detached: boolean
}

/** 流式条目的 id 前缀。用 id 而不是新字段来区分正文 / 思维链，是为了不改契约形状。 */
const STREAM_PREFIX = 'stream:'
const TOOL_PREFIX = 'tool:'

function str(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function numberOr(fallback: number, value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** 事件里的对象字段可能是任何 JSON 值；不是对象时回落到调用方给的默认值。 */
function recordOf(value: unknown, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>
  }
  return fallback
}

function messageText(message: unknown): string {
  const payload = message as MessagePayload | null | undefined
  return str(payload?.content)
}

function subagentOf(value: unknown): ToolRun['subagent'] {
  if (value === null || typeof value !== 'object') return undefined
  const marker = value as { task?: unknown; index?: unknown }
  if (typeof marker.task !== 'string') return undefined
  return { task: marker.task, index: numberOr(0, marker.index) }
}

/**
 * 服务端的工具状态 → 本视图的四档。
 *
 * 线上 `tool_call_finished.status` 取 `schemas.classify_tool_status` 的值
 * （`ok` / `denied` / `failed` / `truncated`），而契约里的 `ToolRun.status` 只有四档：
 * `truncated` 是一次成功调用（结果被截断），归到 `ok`；`failed` 归到 `error`。
 */
function normalizeToolStatus(value: unknown): ToolRun['status'] {
  switch (value) {
    case 'running':
      return 'running'
    case 'denied':
      return 'denied'
    case 'failed':
    case 'error':
      return 'error'
    default:
      return 'ok'
  }
}

/**
 * 历史里的工具状态推断。
 *
 * 只在"没有事件可依赖"时用（刷新 / resync 后从条目重建）：此时拿不到服务端的
 * `tool_call_finished.status`，而条目本身只有内容。规则与
 * `src/avid/web/schemas.py::classify_tool_status` 一一对应——那是线格式的判定点，
 * 这里是它的**离线镜像**；两者一旦分叉，以服务端为准（事件到达时会覆盖）。
 */
function inferToolStatus(content: string): ToolRun['status'] {
  if (content.startsWith('Permission denied.')) return 'denied'
  if (
    content.startsWith('错误：') ||
    content.startsWith('参数错误：') ||
    content.slice(0, 64).includes('执行失败：')
  ) {
    return 'error'
  }
  return 'ok'
}

function parseArguments(raw: unknown, fallback: Record<string, unknown> = {}): Record<string, unknown> {
  if (typeof raw !== 'string' || !raw) return fallback
  try {
    return recordOf(JSON.parse(raw), fallback)
  } catch {
    // 模型给的参数不是合法 JSON：保留空参数，不要因为一个坏参数丢整条工具卡。
    return fallback
  }
}

export function emptyView(sessionId: string | null = null): RunView {
  return {
    sessionId,
    runId: null,
    phase: 'idle',
    seq: 0,
    round: 0,
    tokens: 0,
    usage: null,
    activity: '',
    entries: [],
    tools: [],
    approvals: [],
    compactions: [],
    error: null,
    detached: false,
  }
}

/**
 * 把流式累积提交为普通条目：条目仍在原位置，只是不再标 `streaming`。
 *
 * 与 `applyEvent` 里"durable 消息取代流式条目"区分：那条路径是**丢弃**流式残片，
 * 因为 durable 消息自带完整正文；这里没有完整正文可依赖（终态事件不带正文），
 * 所以只能把已经收到的增量落上去。
 */
export function flushDelta(view: RunView): RunView {
  if (!view.entries.some((entry) => entry.streaming === true)) return view
  return {
    ...view,
    entries: view.entries.map((entry) =>
      entry.streaming === true ? { ...entry, streaming: undefined } : entry,
    ),
  }
}

function streamEntryId(tag: 'content' | 'reasoning', runId: string | null, seq: number): string {
  // 带上 seq：同一条 durable 事件之后到达的 delta 属于新的一段（上一段已被 flush / 取代）。
  return `${STREAM_PREFIX}${tag}:${runId ?? 'pending'}:${seq}`
}

function toolEntryId(toolCallId: string): string {
  return `${TOOL_PREFIX}${toolCallId}`
}

function findTool(view: RunView, toolCallId: string): ToolRun | undefined {
  return view.tools.find((item) => item.toolCallId === toolCallId)
}

/**
 * 工具运行与它的时间线条目一起 upsert。
 *
 * 时间线条目按 `tool:<tool_call_id>` 命名（**不是** durable 的 `entry_id`）：工具结果不是
 * 一条独立对话条目，它挂在自己那张工具卡上——另外起一条同内容的条目会让界面上出现两遍。
 * 用 `tool_call_id` 作去重键天然幂等，重放不会叠卡。
 */
function upsertToolRun(view: RunView, run: ToolRun, ts: number): RunView {
  const tools = view.tools.some((item) => item.toolCallId === run.toolCallId)
    ? view.tools.map((item) => (item.toolCallId === run.toolCallId ? { ...item, ...run } : item))
    : [...view.tools, run]

  const id = toolEntryId(run.toolCallId)
  const patch: TimelineEntry = {
    id,
    kind: 'tool',
    ts,
    text: run.resultText,
    tool: run.tool,
    toolCallId: run.toolCallId,
    args: run.args,
    status: run.status,
  }
  if (run.durationMs !== null) patch.durationMs = run.durationMs

  const entries = view.entries.some((entry) => entry.id === id)
    ? view.entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry))
    : [...view.entries, patch]

  return { ...view, tools, entries }
}

/** assistant 消息里声明的工具调用：与 `viewFromEntries` 走同一条规则，两条路径同形。 */
function declareToolCalls(view: RunView, calls: readonly ToolCallPayload[], ts: number): RunView {
  return calls.reduce((acc, call) => {
    const toolCallId = str(call.id)
    if (!toolCallId) return acc
    const existing = findTool(acc, toolCallId)
    return upsertToolRun(
      acc,
      {
        toolCallId,
        tool: str(call.function?.name) || existing?.tool || '',
        args: parseArguments(call.function?.arguments, existing?.args ?? {}),
        // 已声明过的（可能已经跑完）：不要被后到的复述打回 running。
        status: existing?.status ?? 'running',
        resultText: existing?.resultText ?? '',
        durationMs: existing?.durationMs ?? null,
        startedAt: existing?.startedAt ?? ts,
        subagent: existing?.subagent,
      },
      ts,
    )
  }, view)
}

/** 高层操作：内容仍进 `entries`（`groupTimeline` 负责合并），结果挂到工具卡。 */
function applyMessage(view: RunView, event: EventEnvelope): RunView {
  const message = event.data.message
  const kind: TimelineEntry['kind'] =
    event.type === 'user_message' ? 'user' : event.type === 'assistant_message' ? 'assistant' : 'tool'
  const text = messageText(message)

  // durable 消息自带完整正文：同一条消息的流式残片被它取代（留着会显示两遍）。
  // 思维链条目没有 durable 版本，所以只落定、保留——它是那一轮的过程记录。
  const stripped: RunView = {
    ...view,
    entries: view.entries
      .filter(
        (entry) => !(entry.streaming === true && entry.id.startsWith(`${STREAM_PREFIX}content:`)),
      )
      .map((entry) => (entry.streaming === true ? { ...entry, streaming: undefined } : entry)),
  }

  if (kind === 'tool') {
    const toolCallId = str(message?.tool_call_id)
    const existing = findTool(stripped, toolCallId)
    // 拒绝的结果正文是固定的 'Permission denied.'；已经记下拒绝理由时不让它盖掉。
    const keepDenial = existing?.status === 'denied' && existing.resultText !== ''
    return upsertToolRun(
      stripped,
      {
        toolCallId,
        tool: existing?.tool ?? '',
        args: existing?.args ?? {},
        status: keepDenial ? 'denied' : inferToolStatus(text),
        resultText: keepDenial ? existing!.resultText : text,
        durationMs: existing?.durationMs ?? null,
        startedAt: existing?.startedAt ?? event.ts,
        subagent: existing?.subagent,
      },
      event.ts,
    )
  }

  const id = event.data.entry_id ?? `${event.run_id}:${event.seq ?? event.ts}`
  const entry: TimelineEntry = { id, kind, ts: event.ts, text }
  const index = stripped.entries.findIndex((item) => item.id === id)
  const withEntry: RunView = {
    ...stripped,
    // 幂等的落点：同一个 `entry_id` 只更新那一条，不追加。
    entries:
      index >= 0
        ? stripped.entries.map((item, position) => (position === index ? entry : item))
        : [...stripped.entries, entry],
  }

  const calls = kind === 'assistant' ? message?.tool_calls : undefined
  return calls && calls.length ? declareToolCalls(withEntry, calls, event.ts) : withEntry
}

/** delta 落进"当前流式条目"；没有就新建一条。 */
function appendDelta(view: RunView, event: EventEnvelope): RunView {
  const text = str(event.data.text)
  if (!text) return view
  const tag = event.type === 'reasoning_delta' ? 'reasoning' : 'content'
  const id = streamEntryId(tag, view.runId, view.seq)
  const index = view.entries.findIndex((entry) => entry.id === id && entry.streaming === true)

  if (index >= 0) {
    return {
      ...view,
      entries: view.entries.map((entry, position) =>
        position === index ? { ...entry, text: entry.text + text } : entry,
      ),
    }
  }
  return {
    ...view,
    entries: [...view.entries, { id, kind: 'assistant', ts: event.ts, text, streaming: true }],
  }
}

/**
 * 提醒条目的去重键。
 *
 * 优先用 `entry_id`：`stop_nudge` 的线上载荷带落盘条目 id（`svc/runs.py` 的
 * `_message_sink`），与消息事件走同一条去重规则。
 *
 * 压缩事件没有 `entry_id`，只能用"这条通知说的是什么"作身份（类型 + 时间 + 压缩步）。
 * 两次不同的压缩不可能同一毫秒、同一步：同一条重放则必然同键。
 */
function noticeId(event: EventEnvelope): string {
  const entryId = str(event.data.entry_id)
  if (entryId) return `notice:${entryId}`
  if (event.type === 'context_compacted') {
    return `notice:compaction:${event.ts}:${str(event.data.step)}`
  }
  return `notice:${event.type}:${event.seq ?? event.ts}`
}

/** 事件 → 新状态。不修改入参。 */
export function applyEvent(view: RunView, event: EventEnvelope): RunView {
  // durable 重放的快路径：同一段历史再发一遍时不重建视图（幂等的第一道闸，省重渲染）。
  // 幂等本身不依赖它——下面各分支的去重键才是权威。
  if (event.seq !== null && event.seq <= view.seq) return view

  const base: RunView = {
    ...view,
    runId: event.run_id || view.runId,
    sessionId: event.session_id || view.sessionId,
    seq: event.seq === null ? view.seq : Math.max(view.seq, event.seq),
  }

  switch (event.type) {
    case 'run_started':
      // 新一轮：清掉上一次的错误与 detached。历史条目不动（视图按 run 重建由上层决定）。
      return { ...flushDelta(base), phase: 'running', error: null, detached: false }

    case 'user_message':
    case 'assistant_message':
    case 'tool_result_message':
      return applyMessage(base, event)

    case 'assistant_delta':
    case 'reasoning_delta':
      // 易失通道：不更新 seq、不 flush，直接合成/追加。
      return appendDelta(base, event)

    case 'tool_call_started':
      return upsertToolRun(
        flushDelta(base),
        {
          toolCallId: str(event.data.tool_call_id),
          tool: str(event.data.tool),
          args: recordOf(event.data.arguments),
          status: 'running',
          resultText: '',
          durationMs: null,
          startedAt: event.ts,
          subagent: subagentOf(event.data.subagent),
        },
        event.ts,
      )

    case 'tool_call_finished': {
      const next = flushDelta(base)
      const toolCallId = str(event.data.tool_call_id)
      const existing = findTool(next, toolCallId)
      return upsertToolRun(
        next,
        {
          toolCallId,
          tool: str(event.data.tool) || existing?.tool || '',
          args: recordOf(event.data.arguments, existing?.args ?? {}),
          status: normalizeToolStatus(event.data.status),
          // 线上这条事件的 `content` 被服务端丢掉了（`schemas.event_payload` 只留
          // `content_chars`），所以真正的结果正文由随后的 `tool_result_message` 补。
          resultText: str(event.data.content) || existing?.resultText || '',
          durationMs: numberOrNull(event.data.duration_ms),
          startedAt: existing?.startedAt ?? event.ts,
          subagent: subagentOf(event.data.subagent) ?? existing?.subagent,
        },
        event.ts,
      )
    }

    case 'tool_call_denied': {
      const next = flushDelta(base)
      const toolCallId = str(event.data.tool_call_id)
      const existing = findTool(next, toolCallId)
      return upsertToolRun(
        next,
        {
          toolCallId,
          tool: str(event.data.tool) || existing?.tool || '',
          args: recordOf(event.data.arguments, existing?.args ?? {}),
          status: 'denied',
          // 拒绝理由只有这条事件带，而契约里的 `ToolRun` 没有 reason 字段，
          // 所以把它存进 `resultText`（离线重建时只能拿到 'Permission denied.'）。
          resultText: str(event.data.reason) || existing?.resultText || '',
          durationMs: null,
          startedAt: existing?.startedAt ?? event.ts,
          subagent: subagentOf(event.data.subagent) ?? existing?.subagent,
        },
        event.ts,
      )
    }

    case 'approval_requested': {
      const next = flushDelta(base)
      const approval: Approval = {
        approval_id: str(event.data.approval_id),
        tool: str(event.data.tool),
        arguments: recordOf(event.data.arguments),
        reason: str(event.data.reason),
        created_at: numberOr(event.ts, event.data.created_at),
        expires_at: numberOr(0, event.data.expires_at),
        decision: null,
        resolved_at: null,
        resolved_reason: null,
      }
      // 按 approval_id upsert：SSE 重连会重发 durable 的 approval_requested，
      // 追加式实现会叠出两条待决审批。
      const exists = next.approvals.some((item) => item.approval_id === approval.approval_id)
      return {
        ...next,
        phase: 'awaiting_approval',
        approvals: exists
          ? next.approvals.map((item) =>
              item.approval_id === approval.approval_id ? approval : item,
            )
          : [...next.approvals, approval],
      }
    }

    case 'approval_resolved': {
      const next = flushDelta(base)
      const approvalId = str(event.data.approval_id)
      return {
        ...next,
        // 已决项从待决队列里移除：`approvals` 的语义与 GET /runs/{id}/approvals 相同
        // （只有待决的），留在数组里会被审批条当成还没答复。
        approvals: next.approvals.filter((item) => item.approval_id !== approvalId),
        phase: next.phase === 'awaiting_approval' ? 'running' : next.phase,
      }
    }

    case 'context_compacted': {
      const next = flushDelta(base)
      const id = noticeId(event)
      // 重放：notice 条目的 id 就是去重键，compactions 不会再记第二条。
      if (next.entries.some((entry) => entry.id === id)) return next

      const step = str(event.data.step)
      const detail = str(event.data.detail)
      const before = numberOr(0, event.data.before)
      const after = numberOr(0, event.data.after)
      const text = `${['上下文压缩', step, detail].filter(Boolean).join(' · ')}（${before} → ${after} tok）`
      return {
        ...next,
        compactions: [...next.compactions, { ts: event.ts, step, before, after }],
        entries: [...next.entries, { id, kind: 'notice', notice: 'compaction', ts: event.ts, text }],
      }
    }

    case 'todo_reminder':
    case 'stop_nudge': {
      const next = flushDelta(base)
      const id = noticeId(event)
      if (next.entries.some((entry) => entry.id === id)) return next

      // 注入的提醒是内核做了什么，不是用户说的话；档位用来让界面选图标与文案。
      const notice: TimelineEntry['notice'] = event.type === 'todo_reminder' ? 'todo' : 'nudge'
      const text =
        str(event.data.content) || str(event.data.text) || str(event.data.detail) || str(event.data.reason)
      return { ...next, entries: [...next.entries, { id, kind: 'notice', notice, ts: event.ts, text }] }
    }

    case 'run_status':
      // 子运行的状态事件不碰父视图的轮次 / 用量 / 活动（阶段 30c）：它由 subagent 卡消费；
      // 父运行的这些值只来自父循环自己的 run_status。
      if (event.data.subagent) return base
      return {
        ...base,
        round: numberOr(base.round, event.data.round),
        tokens: numberOr(base.tokens, event.data.tokens),
        // 快照缺失时保留上一份：状态事件不该把已知的读数抹成空。
        usage: event.data.usage ?? base.usage,
        activity: str(event.data.activity) || base.activity,
      }

    case 'run_finished': {
      // 终态不带正文（正文由 assistant_message 落过），所以顺序是"先 flush 再改 phase"。
      const next = flushDelta(base)
      return {
        ...next,
        phase: 'finished',
        tokens: numberOr(next.tokens, event.data.tokens),
        usage: event.data.usage ?? next.usage,
      }
    }

    case 'run_failed': {
      const next = flushDelta(base)
      return {
        ...next,
        phase: 'failed',
        // 失败必须留痕：只改 phase 的失败在界面上等于"悄悄结束"。
        error: { code: str(event.data.code) || 'internal', message: str(event.data.message) },
      }
    }

    case 'run_cancelled':
      return { ...flushDelta(base), phase: 'cancelled' }

    case 'resync':
      // 缓冲区被淘汰 / 游标断档：内容补齐由上层重新拉条目（`viewFromEntries`），
      // 这里只把"历史可能不完整"这件事标出来。
      return { ...flushDelta(base), detached: true }

    default:
      // 事件名单是内核契约；出现未知类型说明两侧漂移。不抛错（一个陌生事件不该让界面白屏），
      // 但也绝不假装处理过它——通用字段已更新，其余原样返回。
      return base
  }
}

/**
 * 用条目重建视图（刷新、resync、断线对账都走它：权威视图来自条目）。
 *
 * 与事件路径的差别只有两处，都是**信息本身不在磁盘上**导致的：
 *   · 工具耗时（`duration_ms`）只活在事件里，重建时是 `null`；
 *   · 内核注入的提醒条目只记了"这是提醒"，没记是哪一种（stop_nudge / todo_reminder），
 *     所以档位回落到 `info`。
 */
export function viewFromEntries(view: RunView, entries: Entry[]): RunView {
  let next: RunView = { ...view, entries: [], tools: [], detached: false }

  for (const entry of [...entries].sort((a, b) => a.seq - b.seq)) {
    next = { ...next, seq: Math.max(next.seq, entry.seq) }

    if (entry.type === 'notice') {
      next = {
        ...next,
        entries: [
          ...next.entries,
          {
            id: entry.entry_id,
            kind: 'notice',
            ts: entry.timestamp,
            text: messageText(entry.message),
            notice: 'info',
          },
        ],
      }
      continue
    }

    if (entry.type !== 'message' || entry.message === null) continue
    const message = entry.message as unknown as MessagePayload
    const text = messageText(message)
    const role = message.role

    if (role === 'tool') {
      const toolCallId = str(message.tool_call_id)
      const existing = findTool(next, toolCallId)
      const keepDenial = existing?.status === 'denied' && existing.resultText !== ''
      next = upsertToolRun(
        next,
        {
          toolCallId,
          tool: existing?.tool ?? '',
          args: existing?.args ?? {},
          // 离线侧只能按内容前缀推断状态（镜像服务端的 classify_tool_status）。
          status: keepDenial ? 'denied' : inferToolStatus(text),
          resultText: keepDenial ? existing!.resultText : text,
          durationMs: existing?.durationMs ?? null,
          startedAt: existing?.startedAt ?? entry.timestamp,
          subagent: existing?.subagent,
        },
        entry.timestamp,
      )
      continue
    }

    const kind: TimelineEntry['kind'] = role === 'user' ? 'user' : 'assistant'
    next = {
      ...next,
      entries: [...next.entries, { id: entry.entry_id, kind, ts: entry.timestamp, text }],
    }

    if (kind === 'assistant' && message.tool_calls && message.tool_calls.length) {
      next = declareToolCalls(next, message.tool_calls, entry.timestamp)
    }
  }

  return next
}
